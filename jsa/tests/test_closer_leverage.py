"""`research_lab/hypotheses/closer_leverage/` -- Modulo 1 del Game Flow
Research Lab. FakeProvider deterministico (reusado de
`test_historical_point_in_time.py`, mismo patron que el resto de
`historical/`), nunca red real."""

from __future__ import annotations

import datetime

import pytest

from jsa.historical import db as historical_db
from jsa.research_lab.hypotheses.closer_leverage.backfill import backfill_season, fetch_team_closer_leverage
from jsa.research_lab.hypotheses.closer_leverage.evaluate import (
    _recompute_bullpen_advantage_with_fatigue,
    evaluate_closer_leverage_hypothesis,
    load_closer_leverage_records,
    run_closer_leverage_hypothesis,
)
from jsa.tests.test_historical_point_in_time import FakeProvider


@pytest.fixture()
def hist_url(tmp_path):
    return f"sqlite:///{tmp_path}/jsa_closer_leverage_test.db"


def test_fetch_team_closer_leverage_with_identified_closer():
    provider = FakeProvider(closer_pitcher_id=999, recent_ip=1.2)
    result = fetch_team_closer_leverage(provider, team_id=147, as_of_date="2022-06-01", season=2022, days=2)
    assert result == {"closer_pitcher_id": 999, "closer_recent_ip": 1.2}


def test_fetch_team_closer_leverage_no_closer_identified():
    provider = FakeProvider(closer_pitcher_id=None)
    result = fetch_team_closer_leverage(provider, team_id=147, as_of_date="2022-06-01", season=2022)
    assert result == {"closer_pitcher_id": None, "closer_recent_ip": None}


def _seed_game(engine, game_pk: int, season: int, home_team_id: int, away_team_id: int) -> None:
    historical_db.init_historical_storage(engine)
    historical_db.upsert_game(
        engine, season=season, game_pk=game_pk, game_date=datetime.date(season, 5, 1),
        home_team="H", away_team="A", home_team_id=home_team_id, away_team_id=away_team_id,
        home_pitcher_id=1, away_pitcher_id=2, is_double_header=0,
    )
    historical_db.update_game_result(engine, game_pk, 5, 3)


def test_backfill_season_writes_one_row_per_team_per_game(hist_url):
    engine = historical_db.get_engine(hist_url)
    _seed_game(engine, 1, 2022, 147, 111)
    provider = FakeProvider(closer_pitcher_id=999, recent_ip=1.0)

    result = backfill_season(engine, provider, 2022, days=2)
    assert result == {"season": 2022, "n_games": 1, "n_processed": 2, "n_skipped": 0, "lookback_days": 2}

    rows = historical_db.closer_leverage_for_season(engine, 2022)
    assert len(rows) == 2
    assert {r["team_id"] for r in rows} == {147, 111}
    assert all(r["closer_pitcher_id"] == 999 and r["closer_recent_ip"] == 1.0 for r in rows)


def test_backfill_season_is_idempotent_never_refetches(hist_url):
    engine = historical_db.get_engine(hist_url)
    _seed_game(engine, 1, 2022, 147, 111)
    provider = FakeProvider(closer_pitcher_id=999, recent_ip=1.0)

    backfill_season(engine, provider, 2022, days=2)
    n_calls_after_first = len(provider.calls)
    result = backfill_season(engine, provider, 2022, days=2)

    assert result["n_processed"] == 0
    assert result["n_skipped"] == 2
    assert len(provider.calls) == n_calls_after_first  # ninguna llamada de red nueva
    assert len(historical_db.closer_leverage_for_season(engine, 2022)) == 2  # nunca duplica


def test_recompute_bullpen_advantage_with_fatigue_penalizes_recent_workload():
    snapshot = {
        "home_bullpen_era": 4.00, "away_bullpen_era": 4.00,
        "home_bullpen_ip_sample": 200.0, "away_bullpen_ip_sample": 200.0,
        "home_closer_available": True, "away_closer_available": True,
        "league_avg_era": 4.30,
    }
    # Sin fatiga -- ERAs identicos -> advantage 0.
    baseline = _recompute_bullpen_advantage_with_fatigue(snapshot, 0.0, 0.0, fatigue_penalty_per_ip=0.10, original_bullpen_advantage=0)
    assert baseline == 0

    # Cerrador HOME fatigado (5 IP en la ventana reciente, toca el techo del penalty) -> penaliza a home ->
    # ventaja para away (negativo).
    fatigued_home = _recompute_bullpen_advantage_with_fatigue(snapshot, 5.0, 0.0, fatigue_penalty_per_ip=0.10, original_bullpen_advantage=0)
    assert fatigued_home < 0


def test_recompute_bullpen_advantage_fatigue_never_exceeds_unavailable_penalty():
    """El penalty de fatiga esta acotado al mismo techo que "cerrador
    lesionado" -- una IP reciente absurdamente alta no puede penalizar mas
    que la ausencia total."""
    snapshot = {
        "home_bullpen_era": 4.00, "away_bullpen_era": 4.00,
        "home_bullpen_ip_sample": 200.0, "away_bullpen_ip_sample": 200.0,
        "home_closer_available": True, "away_closer_available": False,  # away sin closer -> penalty fijo 0.30
        "league_avg_era": 4.30,
    }
    result = _recompute_bullpen_advantage_with_fatigue(snapshot, 50.0, 0.0, fatigue_penalty_per_ip=0.10, original_bullpen_advantage=0)
    # Ambos penalties tocan el mismo techo (0.30) -> vuelven a quedar simetricos -> advantage 0.
    assert result == 0


def test_recompute_bullpen_advantage_falls_back_when_no_bullpen_era():
    snapshot = {"home_bullpen_era": None, "away_bullpen_era": None}
    result = _recompute_bullpen_advantage_with_fatigue(snapshot, 5.0, 0.0, fatigue_penalty_per_ip=0.10, original_bullpen_advantage=1)
    assert result == 1  # se conserva el advantage original, nunca se inventa


def test_load_closer_leverage_records_excludes_games_without_backfill(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    result = load_closer_leverage_records(engine, hist_url, [2022])
    assert result == []


def test_evaluate_closer_leverage_hypothesis_empty_records_no_crash():
    result = evaluate_closer_leverage_hypothesis([])
    assert result["n_games"] == 0
    assert result["report"].retained_in_lab is False


def test_run_closer_leverage_hypothesis_no_backfill_returns_error(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    result = run_closer_leverage_hypothesis([2022], hist_url)
    assert result == {"n_games": 0, "seasons_used": [2022], "error": "no_games_with_closer_leverage_backfill"}
