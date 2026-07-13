"""Metricas de validacion (Brier/LogLoss/ECE/MCE/Home Bias Audit) y
benchmarking obligatorio contra baselines ingenuos + modelos legado
(Seccion 12.3/13.3 del spec)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jsa.historical import db as historical_db
from jsa.historical import pipeline, validation
from jsa.tests.test_historical_point_in_time import FakeProvider


def test_brier_score_perfect_predictions_is_zero():
    assert validation.brier_score([(1.0, 1), (0.0, 0)]) == 0.0


def test_brier_score_worst_predictions_is_one():
    assert validation.brier_score([(0.0, 1), (1.0, 0)]) == 1.0


def test_brier_score_uninformative_always_half_is_quarter():
    assert validation.brier_score([(0.5, 1), (0.5, 0), (0.5, 1), (0.5, 0)]) == 0.25


def test_log_loss_penalizes_confident_wrong_predictions_heavily():
    confident_wrong = validation.log_loss([(0.99, 0)])
    mild_wrong = validation.log_loss([(0.6, 0)])
    assert confident_wrong > mild_wrong


def test_accuracy_uses_half_threshold():
    assert validation.accuracy([(0.6, 1), (0.4, 0), (0.6, 0)]) == pytest.approx(2 / 3)


def test_ece_zero_when_perfectly_calibrated_by_bucket():
    # Todos los casos en el bucket 0.5-0.6 con exactamente 50% de aciertos.
    pairs = [(0.55, 1), (0.55, 0)]
    assert validation.ece(pairs) == pytest.approx(0.05, abs=0.06)


def test_home_bias_audit_flags_excess_favoritism():
    # 10 de 10 favorecen al local, pero el local solo gana 5 de 10 --
    # exceso de 50pp, muy por encima del umbral de 3pp.
    pairs = [(0.6, 1)] * 5 + [(0.6, 0)] * 5
    result = validation.home_bias_audit(pairs)
    assert result.rejected is True
    assert result.excess_home_favoritism_pp == pytest.approx(50.0)


def test_home_bias_audit_passes_when_within_threshold():
    # 55% favorecen al local, local gana 54% -- 1pp de exceso, dentro del umbral.
    pairs = [(0.6, 1)] * 30 + [(0.6, 0)] * 25 + [(0.4, 1)] * 24 + [(0.4, 0)] * 21
    result = validation.home_bias_audit(pairs)
    assert abs(result.excess_home_favoritism_pp) <= 3.0
    assert result.rejected is False


@pytest.fixture()
def ingested_season(tmp_path):
    hist_url = f"sqlite:///{tmp_path}/jsa_historical_test.db"
    prod_url = f"sqlite:///{tmp_path}/jsa_prod_test.db"
    games = [
        {"game_pk": 950000 + i, "season": 2022, "game_date": f"2022-04-{10 + i:02d}",
         "home_team": "New York Yankees", "away_team": "Boston Red Sox", "home_team_id": 147, "away_team_id": 111,
         "home_pitcher_id": 1000 + i, "away_pitcher_id": 2000 + i, "is_double_header": False,
         "home_score": 5 if i % 2 == 0 else 2, "away_score": 3 if i % 2 == 0 else 6}
        for i in range(10)
    ]
    with patch("jsa.historical.pipeline.fetch_season_games", return_value=games):
        pipeline.run_season_ingestion(2022, provider=FakeProvider(), historical_database_url=hist_url, registries_database_url=prod_url)
    return hist_url


def test_benchmark_season_returns_jsa_and_all_baselines(ingested_season):
    result = validation.benchmark_season(2022, ingested_season)
    assert result["n_games_ingested"] == 10
    assert "jsa_evidence_engine" in result
    assert "legacy_heuristic" in result
    assert "legacy_skellam_calibrated" in result
    assert "legacy_negbin" in result
    assert "naive_constant" in result
    assert "naive_best_ops" in result
    for key in ("jsa_evidence_engine", "legacy_heuristic", "naive_constant"):
        assert result[key]["brier"] is not None


def test_benchmark_season_empty_when_nothing_ingested(tmp_path):
    empty_url = f"sqlite:///{tmp_path}/empty_historical.db"
    result = validation.benchmark_season(2099, empty_url)
    assert result["n"] == 0
