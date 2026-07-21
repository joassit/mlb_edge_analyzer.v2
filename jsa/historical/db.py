"""Base de datos propia de `jsa/historical/` -- Feature Store + Results
Store historicos, completamente separados de `jsa/storage/database.py`
(produccion en vivo). Nunca comparten motor de conexion ni archivo por
default (Seccion 4.2 del spec: Feature Store y Results Store nunca se
mezclan; aqui ademas se separa produccion-vs-historico, mismo criterio de
aislamiento que `historical_engine/db.py` en el proyecto hermano)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Column, Date, DateTime, Float, Integer, MetaData, String, Table, UniqueConstraint, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from jsa.storage.dialect_utils import insert_ignore_duplicates

metadata = MetaData()

historical_game = Table(
    "historical_game", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("season", Integer, nullable=False),
    Column("game_pk", Integer, nullable=False),
    Column("game_date", Date, nullable=False),
    Column("home_team", String, nullable=False),
    Column("away_team", String, nullable=False),
    Column("home_team_id", Integer, nullable=False),
    Column("away_team_id", Integer, nullable=False),
    Column("home_pitcher_id", Integer, nullable=True),
    Column("away_pitcher_id", Integer, nullable=True),
    Column("is_double_header", Integer, nullable=False, default=0),
    Column("home_score", Integer, nullable=True),
    Column("away_score", Integer, nullable=True),
    Column("winner", String, nullable=True),
    UniqueConstraint("game_pk", name="uq_historical_game_pk"),
)

historical_snapshot = Table(
    "historical_snapshot", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("season", Integer, nullable=False),
    Column("game_pk", Integer, nullable=False),
    Column("game_date", Date, nullable=False),
    Column("snapshot_hash", String, nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("game_pk", name="uq_historical_snapshot_game_pk"),
)

historical_report = Table(
    "historical_report", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("run_id", String, nullable=False),
    Column("season", Integer, nullable=False),
    Column("game_pk", Integer, nullable=False),
    Column("game_date", Date, nullable=False),
    Column("manifest_status", String, nullable=False),
    Column("output_hash", String, nullable=True),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("game_pk", "run_id", name="uq_historical_report_identity"),
)

historical_season_run = Table(
    "historical_season_run", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("run_id", String, nullable=False, unique=True),
    Column("season", Integer, nullable=False),
    Column("status", String, nullable=False),  # running / completed / failed
    Column("games_total", Integer, nullable=True),
    Column("games_processed", Integer, nullable=False, default=0),
    Column("games_errors", Integer, nullable=False, default=0),
    Column("started_at", DateTime, nullable=False),
    Column("completed_at", DateTime, nullable=True),
)

# Tabla NUEVA (nunca ALTER TABLE sobre una tabla ya existente en el
# historico real -- create_all() no agrega columnas a una tabla que ya
# existe, asi que versionar la corrida vive en una tabla propia en vez de
# columnas nuevas en historical_season_run) -- que version de codigo/
# schema/proveedor produjo cada corrida de ingesta, para poder comparar
# "antes vs despues" de un cambio como el de Trend sin ambiguedad.
historical_ingestion_run_metadata = Table(
    "historical_ingestion_run_metadata", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("run_id", String, nullable=False),
    Column("season", Integer, nullable=False),
    Column("commit_sha", String, nullable=True),
    Column("schema_version", String, nullable=False),
    Column("provider_version", String, nullable=False),
    Column("forced_reingestion", Integer, nullable=False, default=0),
)

# Tabla nueva y aislada para la Etapa 2 del spike de Statcast (ver
# jsa/docs/statcast_integration_design.md) -- eventos crudos de bateo
# (batted-ball events) tal como los devuelve Baseball Savant, SIN
# agregar todavia. La agregacion point-in-time-safe (cumulativa por
# temporada para H1/H2/H3, rolling 7d/14d para H4) se hace en Python
# desde esta tabla, exactamente igual que Elo/Pythagorean/head-to-head
# se calculan desde `historical_game` ya ingerido -- nunca se le pide a
# la fuente un recorte "como si fuera" point-in-time (Baseball Savant no
# tiene garantia documentada de esa semantica, ver seccion 4 del
# diagnostico del spike de factibilidad). Deliberadamente NO se agrega
# a `GameSnapshot`/`historical_snapshot` -- son candidatos bajo
# evaluacion, no un pilar confirmado (mismo criterio que Elo/Pythagorean
# nunca tocaron `team_quality` hasta demostrar mejora).
historical_statcast_event = Table(
    "historical_statcast_event", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("season", Integer, nullable=False),
    Column("game_pk", Integer, nullable=False),
    Column("game_date", Date, nullable=False),
    Column("at_bat_number", Integer, nullable=False),
    Column("pitch_number", Integer, nullable=False),
    Column("inning_topbot", String, nullable=True),
    Column("batter_id", Integer, nullable=True),
    Column("pitcher_id", Integer, nullable=True),
    Column("launch_speed", Float, nullable=True),
    Column("xwoba", Float, nullable=True),
    UniqueConstraint("game_pk", "at_bat_number", "pitch_number", name="uq_historical_statcast_event"),
)


# Game Flow Research Lab -- Closer Leverage Engine (2026-07-21): un
# registro por equipo por juego con el `closer_pitcher_id` real y su IP
# reciente (`days` dias, point-in-time-safe via `pitcher_recent_ip_as_of`)
# -- nunca en `GameSnapshot`/`historical_snapshot` (candidato bajo
# evaluacion en `jsa/research_lab/`, no un pilar confirmado, mismo
# criterio que `historical_statcast_event`). Requiere re-derivar
# `closer_pitcher_id` con una llamada de red real (no se persistio en la
# ingesta original) -- ver `research_lab/hypotheses/closer_leverage/
# backfill.py`.
historical_closer_leverage = Table(
    "historical_closer_leverage", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("season", Integer, nullable=False),
    Column("game_pk", Integer, nullable=False),
    Column("team_id", Integer, nullable=False),
    Column("side", String, nullable=False),  # "home" | "away"
    Column("closer_pitcher_id", Integer, nullable=True),
    Column("closer_recent_ip", Float, nullable=True),
    Column("lookback_days", Integer, nullable=False),
    UniqueConstraint("game_pk", "team_id", name="uq_historical_closer_leverage"),
)


def get_engine(database_url: str) -> Engine:
    return create_engine(database_url, future=True)


def init_historical_storage(engine: Engine) -> None:
    metadata.create_all(engine)


def upsert_game(engine: Engine, **fields) -> None:
    with engine.begin() as conn:
        insert_ignore_duplicates(conn, historical_game, recorded_at=datetime.now(timezone.utc), **fields)


def update_game_result(engine: Engine, game_pk: int, home_score: int, away_score: int) -> None:
    winner = "home" if home_score > away_score else "away"
    with engine.begin() as conn:
        conn.execute(
            historical_game.update()
            .where(historical_game.c.game_pk == game_pk)
            .values(home_score=home_score, away_score=away_score, winner=winner)
        )


def persist_historical_snapshot(engine: Engine, *, season: int, game_pk: int, game_date: date, snapshot_hash: str, snapshot_payload: dict) -> None:
    """Guarda el `GameSnapshot` reconstruido junto al reporte -- necesario
    para que `validation.py` pueda correr los modelos legado (Seccion
    12.3) sobre EXACTAMENTE los mismos insumos crudos que uso el Evidence
    Engine, en vez de tener que volver a golpear la API."""
    try:
        with engine.begin() as conn:
            insert_ignore_duplicates(
                conn, historical_snapshot,
                recorded_at=datetime.now(timezone.utc), season=season, game_pk=game_pk, game_date=game_date,
                snapshot_hash=snapshot_hash, payload=snapshot_payload,
            )
    except IntegrityError:
        pass


def snapshots_for_season(engine: Engine, season: int) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(historical_snapshot).where(historical_snapshot.c.season == season)).mappings().all()
    return [dict(r) for r in rows]


def persist_historical_report(engine: Engine, *, run_id: str, season: int, game_pk: int, game_date: date, report_payload: dict) -> None:
    try:
        with engine.begin() as conn:
            insert_ignore_duplicates(
                conn, historical_report,
                recorded_at=datetime.now(timezone.utc), run_id=run_id, season=season, game_pk=game_pk,
                game_date=game_date, manifest_status=report_payload["manifest_status"],
                output_hash=report_payload.get("output_hash"), payload=report_payload,
            )
    except IntegrityError:
        pass


def start_season_run(engine: Engine, run_id: str, season: int, games_total: int | None) -> None:
    with engine.begin() as conn:
        insert_ignore_duplicates(
            conn, historical_season_run,
            recorded_at=datetime.now(timezone.utc), run_id=run_id, season=season, status="running",
            games_total=games_total, games_processed=0, games_errors=0, started_at=datetime.now(timezone.utc),
            completed_at=None,
        )


def finish_season_run(engine: Engine, run_id: str, *, games_processed: int, games_errors: int, status: str = "completed") -> None:
    with engine.begin() as conn:
        conn.execute(
            historical_season_run.update()
            .where(historical_season_run.c.run_id == run_id)
            .values(games_processed=games_processed, games_errors=games_errors, status=status, completed_at=datetime.now(timezone.utc))
        )


def record_ingestion_run_metadata(
    engine: Engine, run_id: str, season: int, *, commit_sha: str | None, schema_version: str, provider_version: str, forced_reingestion: bool,
) -> None:
    """Un insert append-only por corrida -- nunca actualizado despues, para
    poder comparar exactamente que version de codigo/schema/proveedor
    produjo cada temporada re-ingerida."""
    with engine.begin() as conn:
        conn.execute(
            historical_ingestion_run_metadata.insert().values(
                recorded_at=datetime.now(timezone.utc), run_id=run_id, season=season, commit_sha=commit_sha,
                schema_version=schema_version, provider_version=provider_version, forced_reingestion=int(forced_reingestion),
            )
        )


def games_for_season(engine: Engine, season: int) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(historical_game).where(historical_game.c.season == season)).mappings().all()
    return [dict(r) for r in rows]


def bulk_insert_statcast_events(engine: Engine, season: int, events: list[dict]) -> int:
    """Insert masivo (una sola transaccion) de eventos crudos de bateo --
    `events` es una lista de dicts con exactamente las columnas de
    `historical_statcast_event` salvo `row_id`/`recorded_at`/`season`.
    Duplicados (misma `game_pk`+`at_bat_number`+`pitch_number`, ej. una
    re-ingesta sin `clear_statcast_season()` previo) se ignoran de forma
    portable SQLite/Postgres -- mismo criterio que el resto del proyecto,
    pero a nivel de tabla completa (`executemany` vs. fila por fila) por
    volumen: una temporada son decenas de miles de filas, no cientos."""
    if not events:
        return 0
    now = datetime.now(timezone.utc)
    rows = [{**e, "season": season, "recorded_at": now} for e in events]
    with engine.begin() as conn:
        dialect_name = conn.engine.dialect.name
        if dialect_name == "postgresql":
            from sqlalchemy.dialects import postgresql
            stmt = postgresql.insert(historical_statcast_event).on_conflict_do_nothing()
        elif dialect_name == "sqlite":
            from sqlalchemy.dialects import sqlite
            stmt = sqlite.insert(historical_statcast_event).on_conflict_do_nothing()
        else:
            stmt = historical_statcast_event.insert()
        conn.execute(stmt, rows)
    return len(rows)


def clear_statcast_season(engine: Engine, season: int) -> int:
    with engine.begin() as conn:
        result = conn.execute(historical_statcast_event.delete().where(historical_statcast_event.c.season == season))
    return result.rowcount


def count_statcast_events_for_season(engine: Engine, season: int) -> int:
    with engine.connect() as conn:
        rows = conn.execute(select(historical_statcast_event.c.row_id).where(historical_statcast_event.c.season == season)).all()
    return len(rows)


def upsert_closer_leverage(engine: Engine, **fields) -> None:
    """Idempotente por `(game_pk, team_id)` -- re-correr el backfill de una
    temporada nunca duplica ni pisa una fila ya calculada (misma disciplina
    de `upsert_game`)."""
    with engine.begin() as conn:
        insert_ignore_duplicates(conn, historical_closer_leverage, recorded_at=datetime.now(timezone.utc), **fields)


def closer_leverage_for_season(engine: Engine, season: int) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(historical_closer_leverage).where(historical_closer_leverage.c.season == season)).mappings().all()
    return [dict(r) for r in rows]


def statcast_events_for_seasons(engine: Engine, seasons: list[int]) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(historical_statcast_event).where(historical_statcast_event.c.season.in_(seasons))).mappings().all()
    return [dict(r) for r in rows]


def reports_for_season(engine: Engine, season: int) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(historical_report).where(historical_report.c.season == season)).mappings().all()
    return [dict(r) for r in rows]


def already_ingested_game_pks(engine: Engine, season: int) -> set[int]:
    """Usado por `pipeline.py` para resumibilidad -- una temporada puede
    tomar horas; si la corrida se corta a mitad de camino (ej. timeout de
    GitHub Actions), una re-ejecucion no debe repetir juegos ya evaluados."""
    with engine.connect() as conn:
        rows = conn.execute(select(historical_report.c.game_pk).where(historical_report.c.season == season)).all()
    return {r[0] for r in rows}


def clear_season(engine: Engine, season: int) -> dict:
    """Borra `historical_snapshot`/`historical_report`/`historical_season_run`
    de una temporada -- usado por `run_season_ingestion(..., force=True)`
    para forzar un reproceso completo cuando la logica de reconstruccion
    cambio (ej. campos nuevos del snapshot) y la resumibilidad de
    `already_ingested_game_pks()` saltaria de largo juegos ya evaluados con
    la logica VIEJA. NUNCA borra `historical_game` (schedule/resultados son
    hechos estables, no cambian con la logica de evaluacion)."""
    with engine.begin() as conn:
        snap_result = conn.execute(historical_snapshot.delete().where(historical_snapshot.c.season == season))
        report_result = conn.execute(historical_report.delete().where(historical_report.c.season == season))
        run_result = conn.execute(historical_season_run.delete().where(historical_season_run.c.season == season))
    return {
        "snapshots_deleted": snap_result.rowcount,
        "reports_deleted": report_result.rowcount,
        "season_runs_deleted": run_result.rowcount,
    }
