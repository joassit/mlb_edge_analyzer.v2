"""Test de integracion de punta a punta: `main.run_daily()` con la capa de
red completamente mockeada, sobre un juego falso. Verifica, asercion por
asercion, las garantias de gobernanza que el plan de esta entrega promete
-- no solo que "no truena"."""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest

from jsa import config
from jsa import main as jsa_main
from jsa.data_sources.injuries import InjuryIndex
from jsa.domain.hashing import hash_value
from jsa.domain.models import SEVEN_PILLARS
from jsa.registries import db as registries_db
from jsa.storage import database as storage_db

FAKE_GAME = {
    "game_pk": 999001,
    "away_team": "Boston Red Sox",
    "home_team": "New York Yankees",
    "away_team_id": 111,
    "home_team_id": 147,
    "away_pitcher_id": None,
    "home_pitcher_id": None,
    "game_time": "2026-07-13T23:05:00Z",
    "game_date_official": "2026-07-13",
    "abstract_state": "Preview",
    "is_double_header": False,
}


@pytest.fixture()
def sqlite_url(tmp_path):
    return f"sqlite:///{tmp_path}/jsa_integration_test.db"


# Un jugador clave del equipo local (147), colocado en IL antes de la
# fecha del juego falso -- alimenta `test_key_injuries_from_preload_reaches_persisted_snapshot`.
FAKE_INJURY_INDEX = InjuryIndex(
    events_by_player={555: [("2026-07-01", "placed")]},
    name_by_player={555: "Fake Injured Player"},
    team_by_player={555: 147},
    is_key_by_player={555: True},
)


def _run_with_mocks(sqlite_url: str):
    with patch.object(config, "DATABASE_URL", sqlite_url), \
         patch("jsa.main.mlb_api.get_schedule", return_value=[FAKE_GAME]), \
         patch("jsa.main.weather.preload_weather", return_value={147: {"temp_f": 75, "wind_mph": 5}}), \
         patch("jsa.main.travel.preload_travel_distances", return_value={111: 200.0}), \
         patch("jsa.main.injuries.build_today_injury_index", return_value=FAKE_INJURY_INDEX), \
         patch("jsa.data_sources.snapshot_builder.stats.get_pitcher_era_ip", return_value=None), \
         patch("jsa.data_sources.snapshot_builder.stats.get_pitcher_command", return_value={}), \
         patch("jsa.data_sources.snapshot_builder.stats.get_team_ops", return_value=0.75), \
         patch("jsa.data_sources.snapshot_builder.stats.get_team_ops_pa_sample", return_value=300), \
         patch(
             "jsa.data_sources.snapshot_builder.stats.get_bullpen_era",
             return_value={"era": 4.0, "closer_pitcher_id": 999},
         ), \
         patch("jsa.data_sources.snapshot_builder.stats.get_league_era", return_value=4.3), \
         patch("jsa.data_sources.snapshot_builder.stats.get_league_ops", return_value=0.75), \
         patch("jsa.data_sources.snapshot_builder.stats.get_league_runs_per_game", return_value=4.5):
        return jsa_main.run_daily(target_date=datetime.date(2026, 7, 13))


def test_run_daily_processes_the_single_game(sqlite_url):
    summary = _run_with_mocks(sqlite_url)
    assert summary["games"] == 1
    assert summary["processed"] == 1
    assert summary["errors"] == 0


def _load_report(sqlite_url: str):
    engine = registries_db.get_engine(sqlite_url)
    rows = storage_db.reports_for_date(engine, datetime.date(2026, 7, 13))
    assert len(rows) == 1
    return rows[0]["payload"]


def test_clean_run_produces_valid_manifest_with_pillar_versions(sqlite_url):
    _run_with_mocks(sqlite_url)
    report = _load_report(sqlite_url)

    assert report["manifest_status"] == "valid"
    manifest = report["manifest"]
    assert manifest["invalidated"] is False
    assert manifest["invalidation_reasons"] == []
    assert set(manifest["pillar_versions"].keys()) == set(SEVEN_PILLARS)
    for pillar in SEVEN_PILLARS:
        assert "@" in manifest["pillar_versions"][pillar]


def test_hashes_verify_against_independent_recomputation(sqlite_url):
    _run_with_mocks(sqlite_url)
    report = _load_report(sqlite_url)

    # input_snapshot_hash: recalculado desde el snapshot persistido, no
    # confiado tal cual.
    engine = registries_db.get_engine(sqlite_url)
    from sqlalchemy import select

    with engine.connect() as conn:
        snap_row = conn.execute(
            select(storage_db.game_snapshots).where(storage_db.game_snapshots.c.game_id == report["game_id"])
        ).mappings().first()
    from jsa.domain.models import GameSnapshot

    snapshot = GameSnapshot(**snap_row["payload"])
    assert snapshot.compute_hash() == snapshot.snapshot_hash == report["input_snapshot_hash"]

    # config_hash: recalculado desde config.py de nuevo, independientemente.
    from jsa.engine.orchestrator import compute_config_hash

    assert compute_config_hash(config.BASE_PILLAR_WEIGHTS) == report["config_hash"] == report["manifest"]["config_hash"]

    # output_hash: recalculado desde el propio reporte (misma funcion que
    # usa el sistema, pero re-ejecutada aqui de forma independiente sobre
    # los datos ya persistidos, no sobre el objeto en memoria original).
    from jsa.domain.models import JSAReport

    rebuilt = JSAReport(**report)
    assert rebuilt.compute_output_hash() == report["output_hash"]


def test_travel_distance_from_preload_reaches_persisted_snapshot(sqlite_url):
    _run_with_mocks(sqlite_url)
    engine = registries_db.get_engine(sqlite_url)
    from sqlalchemy import select

    with engine.connect() as conn:
        snap_row = conn.execute(select(storage_db.game_snapshots)).mappings().first()
    assert snap_row["payload"]["travel_distance"] == 200.0


def test_key_injuries_and_closer_available_from_preload_reach_persisted_snapshot(sqlite_url):
    _run_with_mocks(sqlite_url)
    engine = registries_db.get_engine(sqlite_url)
    from sqlalchemy import select

    with engine.connect() as conn:
        snap_row = conn.execute(select(storage_db.game_snapshots)).mappings().first()
    payload = snap_row["payload"]
    # home_team_id=147 tiene un jugador clave lesionado en FAKE_INJURY_INDEX.
    assert payload["home_key_injuries"] == ["Fake Injured Player"]
    assert payload["away_key_injuries"] == []
    # closer_pitcher_id=999 mockeado para ambos equipos, no aparece en
    # FAKE_INJURY_INDEX -- ninguno de los dos cerradores esta lesionado.
    assert payload["home_closer_available"] is True
    assert payload["away_closer_available"] is True


def test_no_experimental_rule_moved_production_weights(sqlite_url):
    _run_with_mocks(sqlite_url)
    report = _load_report(sqlite_url)

    for entry in report["weight_audit"]:
        assert entry["rules_applied"] == []
        assert entry["delta_total"] == 0.0
        assert entry["final_weight"] == entry["base_weight"]

    assert report["base_weights"] == report["final_weights"]


def test_report_is_honestly_uncalibrated(sqlite_url):
    _run_with_mocks(sqlite_url)
    report = _load_report(sqlite_url)

    assert report["calibration"]["calibration_status"] == "uncalibrated"
    assert report["calibration"]["calibrated_probability"] is None
    assert report["final_category"] == "NO_DISPONIBLE_SIN_CALIBRAR"

    assert len(report["confidence_gate"]) == 4
    for gate_result in report["confidence_gate"]:
        assert gate_result["passed"] is False
        assert gate_result["reason"] == "uncalibrated"


def test_reconstruction_token_is_stable_and_derived_from_hashes(sqlite_url):
    _run_with_mocks(sqlite_url)
    report = _load_report(sqlite_url)

    expected = hash_value(
        {"run_id": report["run_id"], "config_hash": report["config_hash"], "input_snapshot_hash": report["input_snapshot_hash"]}
    )
    assert report["reconstruction_token"] == expected
