"""Fusiona N bases historicas SQLite/Postgres separadas (una por
temporada, como las que produce cada corrida aislada de
`jsa_historical_ingest.yml` cuando no hay `JSA_HISTORICAL_DATABASE_URL`
configurado) en una unica base destino con las 5 temporadas juntas.

Existe porque `validation.py`/`monte_carlo.py` leen una temporada a la vez
de UNA base -- son utiles de verdad recien cuando esa base contiene varias
temporadas seguidas para comparar. `game_pk` de la MLB Stats API es unico
de por vida (nunca se reutiliza entre temporadas), asi que fusionar filas
de distintas temporadas no puede pisar una fila de otra -- las tablas
usan `insert_ignore_duplicates` de todas formas para que correr el merge
dos veces sobre la misma base destino sea inofensivo (idempotente), igual
que el resto de `historical/db.py`."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.engine import Engine

from jsa.historical import db as historical_db
from jsa.storage.dialect_utils import insert_ignore_duplicates

_TABLES = (
    historical_db.historical_game,
    historical_db.historical_snapshot,
    historical_db.historical_report,
    historical_db.historical_season_run,
)


def merge_databases(source_urls: list[str], target_database_url: str) -> dict[str, int]:
    target_engine = historical_db.get_engine(target_database_url)
    historical_db.init_historical_storage(target_engine)

    merged_counts: dict[str, int] = {table.name: 0 for table in _TABLES}

    for source_url in source_urls:
        source_engine = historical_db.get_engine(source_url)
        historical_db.init_historical_storage(source_engine)  # tolera una fuente vacia/parcial
        merged_counts = _merge_one_source(source_engine, target_engine, merged_counts)

    return merged_counts


def _merge_one_source(source_engine: Engine, target_engine: Engine, merged_counts: dict[str, int]) -> dict[str, int]:
    for table in _TABLES:
        with source_engine.connect() as source_conn:
            rows = source_conn.execute(select(table)).mappings().all()
        with target_engine.begin() as target_conn:
            for row in rows:
                values = {k: v for k, v in dict(row).items() if k != "row_id"}
                insert_ignore_duplicates(target_conn, table, **values)
        merged_counts[table.name] += len(rows)
    return merged_counts
