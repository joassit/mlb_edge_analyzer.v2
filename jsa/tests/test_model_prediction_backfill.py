"""`historical/model_prediction_backfill.py` -- persiste por juego la
probabilidad de que gane el local segun jsa_evidence_engine + los 3
modelos legado, reusando exactamente `_legacy_predictions()` de
`validation.py`. SQLite real basado en archivo, nunca red."""

from __future__ import annotations

import datetime

import pytest

from jsa.historical import db as historical_db
from jsa.historical.model_prediction_backfill import backfill_season


@pytest.fixture()
def hist_url(tmp_path):
    return f"sqlite:///{tmp_path}/jsa_model_prediction_backfill_test.db"


def _seed_game(engine, game_pk: int, season: int, *, raw_probability: float = 0.6) -> None:
    historical_db.init_historical_storage(engine)
    game_date = datetime.date(season, 5, 1)
    historical_db.upsert_game(
        engine, season=season, game_pk=game_pk, game_date=game_date,
        home_team="Home", away_team="Away", home_team_id=1, away_team_id=2,
        home_pitcher_id=100, away_pitcher_id=200, is_double_header=0,
    )
    historical_db.update_game_result(engine, game_pk, 5, 3)
    historical_db.persist_historical_snapshot(
        engine, season=season, game_pk=game_pk, game_date=game_date,
        snapshot_hash=f"hash-{game_pk}",
        snapshot_payload={
            "game_id": str(game_pk), "game_date": game_date.isoformat(), "season": season,
            "home_team": "Home", "away_team": "Away",
            "league_avg_era": 4.30, "league_avg_ops": 0.750,
            "home_starter_xera": 3.20, "away_starter_xera": 4.10,
            "home_starter_ip_sample": 60.0, "away_starter_ip_sample": 60.0,
            "home_bullpen_era": 3.80, "away_bullpen_era": 4.00,
            "home_bullpen_ip_sample": 90.0, "away_bullpen_ip_sample": 90.0,
            "home_ops": 0.780, "away_ops": 0.740,
            "home_closer_available": True, "away_closer_available": True,
        },
    )
    historical_db.persist_historical_report(
        engine, run_id=f"run-{season}", season=season, game_pk=game_pk, game_date=game_date,
        report_payload={"manifest_status": "valid", "calibration": {"raw_probability": raw_probability}},
    )


def test_backfill_season_no_games_returns_error(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    result = backfill_season(2022, hist_url)
    assert result == {"season": 2022, "n_games": 0, "n_predictions_written": 0, "error": "sin juegos ingeridos -- correr jsa.historical.pipeline primero"}


def test_backfill_season_writes_one_row_per_model(hist_url):
    engine = historical_db.get_engine(hist_url)
    _seed_game(engine, 1, 2022, raw_probability=0.62)

    result = backfill_season(2022, hist_url)
    assert result["n_games"] == 1
    assert result["n_games_scored"] == 1
    assert result["n_predictions_written"] == 4  # jsa_evidence_engine + 3 legado

    rows = historical_db.model_predictions_for_season(engine, 2022)
    by_model = {r["model_name"]: r["home_win_prob"] for r in rows}
    assert set(by_model) == {"jsa_evidence_engine", "legacy_heuristic", "legacy_skellam_calibrated", "legacy_negbin"}
    assert by_model["jsa_evidence_engine"] == pytest.approx(0.62)
    # Cada probabilidad valida esta en [0, 1] -- no es un valor especifico
    # esperado (eso ya lo prueba benchmark_season()/test_calibration.py),
    # solo que la persistencia no corrompe lo que devuelve el modelo real.
    for prob in by_model.values():
        assert 0.0 <= prob <= 1.0


def test_backfill_season_is_idempotent_upsert_overwrites(hist_url):
    engine = historical_db.get_engine(hist_url)
    _seed_game(engine, 1, 2022, raw_probability=0.55)
    backfill_season(2022, hist_url)

    # Re-correr con un raw_probability distinto (simula recalibracion) debe
    # ACTUALIZAR la fila existente, nunca duplicarla.
    historical_db.upsert_model_prediction(engine, season=2022, game_pk=1, model_name="jsa_evidence_engine", home_win_prob=0.99)
    result = backfill_season(2022, hist_url)
    assert result["n_predictions_written"] == 4

    rows = historical_db.model_predictions_for_season(engine, 2022)
    assert len(rows) == 4  # nunca duplica por (game_pk, model_name)
    jsa_row = [r for r in rows if r["model_name"] == "jsa_evidence_engine"][0]
    assert jsa_row["home_win_prob"] == pytest.approx(0.55)  # vuelve a reflejar el valor real, no el manual


def test_backfill_season_skips_games_without_winner(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    game_date = datetime.date(2022, 5, 1)
    historical_db.upsert_game(
        engine, season=2022, game_pk=2, game_date=game_date,
        home_team="Home", away_team="Away", home_team_id=1, away_team_id=2,
        home_pitcher_id=100, away_pitcher_id=200, is_double_header=0,
    )
    # Sin update_game_result() -- winner queda NULL (juego no jugado aun).
    historical_db.persist_historical_snapshot(
        engine, season=2022, game_pk=2, game_date=game_date,
        snapshot_hash="hash-2", snapshot_payload={"league_avg_era": 4.30},
    )
    historical_db.persist_historical_report(
        engine, run_id="run-2022", season=2022, game_pk=2, game_date=game_date,
        report_payload={"manifest_status": "valid", "calibration": {"raw_probability": 0.5}},
    )

    result = backfill_season(2022, hist_url)
    assert result["n_games_scored"] == 0
    assert result["n_predictions_written"] == 0
    assert historical_db.model_predictions_for_season(engine, 2022) == []
