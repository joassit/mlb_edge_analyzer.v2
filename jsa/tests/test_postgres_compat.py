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


def test_pillar_contribution_analyzer_gives_identical_results_on_postgres_and_sqlite(tmp_path):
    """`PillarContributionAnalyzer` en si es puro (sin I/O), pero su lector
    (`historical/pillar_contribution.py`) si depende del dialecto via
    `historical_db.get_engine()`/`reports_for_season()` -- este test ingiere
    la MISMA temporada sintetica contra SQLite y contra Postgres real y
    exige resultados bit-a-bit identicos, no solo "que no explote"."""
    from dataclasses import asdict
    from unittest.mock import patch

    from jsa.historical import pipeline
    from jsa.historical.pillar_contribution import analyze_season_pillar_contribution
    from jsa.tests.test_historical_point_in_time import FakeProvider

    games = [
        {"game_pk": 5100000 + i, "season": 2022, "game_date": f"2022-07-{10 + i:02d}",
         "home_team": "New York Yankees", "away_team": "Boston Red Sox", "home_team_id": 147, "away_team_id": 111,
         "home_pitcher_id": 1000 + i, "away_pitcher_id": 2000 + i, "is_double_header": False,
         "home_score": 5 if i % 2 == 0 else 2, "away_score": 3 if i % 2 == 0 else 6}
        for i in range(6)
    ]
    sqlite_url = f"sqlite:///{tmp_path}/parity_hist.db"
    prod_url = f"sqlite:///{tmp_path}/parity_prod.db"

    with patch("jsa.historical.pipeline.fetch_season_games", return_value=games):
        pipeline.run_season_ingestion(2022, provider=FakeProvider(), historical_database_url=sqlite_url, registries_database_url=prod_url)
        pipeline.run_season_ingestion(2022, provider=FakeProvider(), historical_database_url=TEST_POSTGRES_URL, registries_database_url=prod_url)

    report_sqlite = analyze_season_pillar_contribution(2022, sqlite_url)
    report_postgres = analyze_season_pillar_contribution(2022, TEST_POSTGRES_URL)

    assert asdict(report_sqlite) == asdict(report_postgres)
