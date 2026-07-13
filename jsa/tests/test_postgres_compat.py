"""JSA debe poder migrar 100% a Postgres desde el dia 1 (principio rector
de esta entrega) -- este test crea las tablas de los TRES motores de
storage (`registries/db.py`, `storage/database.py`, `historical/db.py`)
contra un Postgres real y ejerce el helper dialect-aware
(`storage/dialect_utils.py::insert_ignore_duplicates`) para confirmar que
un insert duplicado se ignora igual que en SQLite (el gap real que existia
antes de esta entrega: `.prefix_with("OR IGNORE", dialect="sqlite")` no
hacia nada en Postgres).

Se salta automaticamente si `TEST_POSTGRES_URL` no esta configurado -- no
agrega una dependencia dura de tener Postgres corriendo para poder testear
localmente o en CI sin esa infraestructura."""

from __future__ import annotations

import os

import pytest

TEST_POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(not TEST_POSTGRES_URL, reason="TEST_POSTGRES_URL no configurado -- se salta el test de compatibilidad con Postgres")


def test_all_three_storage_engines_create_tables_on_postgres():
    from jsa.historical import db as historical_db
    from jsa.registries import db as registries_db
    from jsa.storage import database as storage_db

    engine = registries_db.get_engine(TEST_POSTGRES_URL)
    registries_db.init_registries(engine)
    storage_db.init_storage(engine)

    historical_engine = historical_db.get_engine(TEST_POSTGRES_URL)
    historical_db.init_historical_storage(historical_engine)


def test_insert_ignore_duplicates_actually_ignores_on_postgres():
    from datetime import date, datetime, timezone

    from jsa.storage import database as storage_db
    from jsa.registries import db as registries_db

    engine = registries_db.get_engine(TEST_POSTGRES_URL)
    storage_db.init_storage(engine)

    values = dict(
        recorded_at=datetime.now(timezone.utc), game_pk=999999999, game_date=date(2022, 1, 1),
        home_score=1, away_score=0, winner="home", total_runs=1,
    )
    storage_db.record_result(engine, game_pk=999999999, game_date=date(2022, 1, 1), home_score=1, away_score=0)
    # Segunda vez con el mismo game_pk/game_date -- debe IGNORARSE, no
    # lanzar IntegrityError (eso es exactamente lo que se rompia antes del
    # fix de esta entrega).
    storage_db.record_result(engine, game_pk=999999999, game_date=date(2022, 1, 1), home_score=1, away_score=0)

    from sqlalchemy import select

    with engine.connect() as conn:
        rows = conn.execute(
            select(storage_db.results).where(storage_db.results.c.game_pk == 999999999)
        ).mappings().all()
    assert len(rows) == 1  # no se duplico
