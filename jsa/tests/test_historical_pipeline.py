"""Pipeline de ingesta historica de punta a punta, con la capa de red
completamente mockeada (`fetch_season_games` reemplazado) -- mismo
espiritu que `tests/test_pipeline_integration.py` para produccion en vivo."""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest

from jsa.historical import db as historical_db
from jsa.historical import pipeline
from jsa.tests.test_historical_point_in_time import FakeProvider

FAKE_GAMES = [
    {
        "game_pk": 900001, "season": 2022, "game_date": "2022-04-10",
        "home_team": "New York Yankees", "away_team": "Boston Red Sox",
        "home_team_id": 147, "away_team_id": 111, "home_pitcher_id": 1001, "away_pitcher_id": 1002,
        "is_double_header": False, "home_score": 5, "away_score": 3,
    },
    {
        "game_pk": 900002, "season": 2022, "game_date": "2022-04-11",
        "home_team": "New York Yankees", "away_team": "Boston Red Sox",
        "home_team_id": 147, "away_team_id": 111, "home_pitcher_id": 1003, "away_pitcher_id": 1004,
        "is_double_header": False, "home_score": 2, "away_score": 6,
    },
]


@pytest.fixture()
def isolated_dbs(tmp_path):
    hist_url = f"sqlite:///{tmp_path}/jsa_historical_test.db"
    prod_url = f"sqlite:///{tmp_path}/jsa_prod_test.db"
    yield hist_url, prod_url


def test_season_ingestion_processes_all_games(isolated_dbs):
    hist_url, prod_url = isolated_dbs
    with patch("jsa.historical.pipeline.fetch_season_games", return_value=FAKE_GAMES):
        result = pipeline.run_season_ingestion(
            2022, provider=FakeProvider(), historical_database_url=hist_url, registries_database_url=prod_url,
        )
    assert result["games_total"] == 2
    assert result["processed"] == 2
    assert result["errors"] == 0


def test_season_ingestion_records_run_metadata(isolated_dbs):
    """Cada corrida debe quedar versionada (schema/proveedor/commit) en
    historical_ingestion_run_metadata -- para poder comparar "antes vs
    despues" de un cambio como Trend sin ambiguedad sobre que version
    produjo cada temporada."""
    from jsa.domain.models import SCHEMA_VERSION
    from jsa.historical.point_in_time_provider import PROVIDER_VERSION

    hist_url, prod_url = isolated_dbs
    with patch("jsa.historical.pipeline.fetch_season_games", return_value=FAKE_GAMES):
        result = pipeline.run_season_ingestion(
            2022, provider=FakeProvider(), historical_database_url=hist_url, registries_database_url=prod_url, force=True,
        )

    engine = historical_db.get_engine(hist_url)
    with engine.connect() as conn:
        from sqlalchemy import select

        rows = conn.execute(
            select(historical_db.historical_ingestion_run_metadata)
            .where(historical_db.historical_ingestion_run_metadata.c.run_id == result["run_id"])
        ).mappings().all()

    assert len(rows) == 1
    row = rows[0]
    assert row["schema_version"] == SCHEMA_VERSION
    assert row["provider_version"] == PROVIDER_VERSION
    assert row["forced_reingestion"] == 1
    assert row["season"] == 2022


def test_season_ingestion_is_resumable(isolated_dbs):
    hist_url, prod_url = isolated_dbs
    with patch("jsa.historical.pipeline.fetch_season_games", return_value=FAKE_GAMES):
        pipeline.run_season_ingestion(2022, provider=FakeProvider(), historical_database_url=hist_url, registries_database_url=prod_url)
        second = pipeline.run_season_ingestion(2022, provider=FakeProvider(), historical_database_url=hist_url, registries_database_url=prod_url)
    assert second["already_ingested"] == 2
    assert second["processed"] == 0


def test_season_ingestion_force_reprocesses_already_ingested_games(isolated_dbs):
    """`force=True` debe ignorar la resumibilidad y volver a procesar
    juegos ya ingeridos -- necesario para una re-ingesta real despues de
    un cambio en `reconstruct_snapshot()`/`evaluate_game()` (ver
    `historical_db.clear_season()`)."""
    hist_url, prod_url = isolated_dbs
    with patch("jsa.historical.pipeline.fetch_season_games", return_value=FAKE_GAMES):
        first = pipeline.run_season_ingestion(2022, provider=FakeProvider(), historical_database_url=hist_url, registries_database_url=prod_url)
        assert first["processed"] == 2

        second_no_force = pipeline.run_season_ingestion(2022, provider=FakeProvider(), historical_database_url=hist_url, registries_database_url=prod_url)
        assert second_no_force["processed"] == 0  # resumibilidad normal -- se saltan

        third_forced = pipeline.run_season_ingestion(
            2022, provider=FakeProvider(), historical_database_url=hist_url, registries_database_url=prod_url, force=True,
        )
    assert third_forced["processed"] == 2  # force=True reprocesa todo


def test_season_ingestion_force_replaces_stale_snapshot_payload(isolated_dbs):
    """Sin `clear_season()`, el UniqueConstraint de `historical_snapshot`
    ignoraria silenciosamente el insert nuevo -- este test prueba que
    `force=True` deja el payload NUEVO persistido, no el viejo."""
    hist_url, prod_url = isolated_dbs
    with patch("jsa.historical.pipeline.fetch_season_games", return_value=FAKE_GAMES):
        pipeline.run_season_ingestion(
            2022, provider=FakeProvider(fielding_pct=None), historical_database_url=hist_url, registries_database_url=prod_url,
        )
        pipeline.run_season_ingestion(
            2022, provider=FakeProvider(fielding_pct=0.991), historical_database_url=hist_url, registries_database_url=prod_url,
            force=True,
        )

    engine = historical_db.get_engine(hist_url)
    snapshots = historical_db.snapshots_for_season(engine, 2022)
    assert len(snapshots) == 2
    for row in snapshots:
        assert row["payload"]["home_fielding_pct"] == 0.991


def test_clear_season_deletes_snapshots_and_reports_but_not_games(isolated_dbs):
    hist_url, prod_url = isolated_dbs
    with patch("jsa.historical.pipeline.fetch_season_games", return_value=FAKE_GAMES):
        pipeline.run_season_ingestion(2022, provider=FakeProvider(), historical_database_url=hist_url, registries_database_url=prod_url)

    engine = historical_db.get_engine(hist_url)
    deleted = historical_db.clear_season(engine, 2022)
    assert deleted == {"snapshots_deleted": 2, "reports_deleted": 2, "season_runs_deleted": 1}
    assert historical_db.snapshots_for_season(engine, 2022) == []
    assert historical_db.reports_for_season(engine, 2022) == []
    # historical_game (schedule/resultados) nunca se borra -- son hechos
    # estables, no cambian con la logica de reconstruccion.
    assert len(historical_db.games_for_season(engine, 2022)) == 2


def test_ingested_reports_are_valid_and_use_real_evidence_engine(isolated_dbs):
    hist_url, prod_url = isolated_dbs
    with patch("jsa.historical.pipeline.fetch_season_games", return_value=FAKE_GAMES):
        pipeline.run_season_ingestion(2022, provider=FakeProvider(), historical_database_url=hist_url, registries_database_url=prod_url)

    engine = historical_db.get_engine(hist_url)
    reports = historical_db.reports_for_season(engine, 2022)
    assert len(reports) == 2
    for row in reports:
        payload = row["payload"]
        assert payload["manifest_status"] == "valid"
        assert len(payload["pillar_advantages"]) == 7
        assert payload["calibration"]["calibration_status"] == "uncalibrated"


def test_ingestion_isolated_from_production_game_data(isolated_dbs):
    """La ingesta historica no debe dejar ninguna fila en las tablas de
    juego/reporte de PRODUCCION (jsa/storage/database.py) -- solo puede
    leer los Registries de esa base, nunca escribir datos de juego ahi."""
    hist_url, prod_url = isolated_dbs
    with patch("jsa.historical.pipeline.fetch_season_games", return_value=FAKE_GAMES):
        pipeline.run_season_ingestion(2022, provider=FakeProvider(), historical_database_url=hist_url, registries_database_url=prod_url)

    from jsa.registries import db as registries_db
    from jsa.storage import database as storage_db

    prod_engine = registries_db.get_engine(prod_url)
    storage_db.init_storage(prod_engine)
    reports_today = storage_db.reports_for_date(prod_engine, datetime.date.today())
    assert reports_today == []


def test_one_bad_game_does_not_abort_the_season(isolated_dbs):
    hist_url, prod_url = isolated_dbs
    broken_games = FAKE_GAMES + [{"game_pk": 900003, "season": 2022}]  # falta la mayoria de campos
    with patch("jsa.historical.pipeline.fetch_season_games", return_value=broken_games):
        result = pipeline.run_season_ingestion(2022, provider=FakeProvider(), historical_database_url=hist_url, registries_database_url=prod_url)
    assert result["processed"] == 2
    assert result["errors"] == 1
