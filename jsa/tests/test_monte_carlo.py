"""Monte Carlo Audit -- Seccion 13.7bis del spec."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jsa.domain.models import SEVEN_PILLARS
from jsa.historical import monte_carlo, pipeline
from jsa.tests.test_historical_point_in_time import FakeProvider


def test_pseudo_probability_is_monotonic_and_bounded():
    assert monte_carlo._pseudo_probability(-2) < monte_carlo._pseudo_probability(0) < monte_carlo._pseudo_probability(2)
    assert 0 < monte_carlo._pseudo_probability(-10) < 1
    assert 0 < monte_carlo._pseudo_probability(10) < 1


def test_sample_perturbed_weights_always_sums_to_one():
    import random

    from jsa.config import BASE_PILLAR_WEIGHTS

    rng = random.Random(1)
    for _ in range(50):
        weights = monte_carlo._sample_perturbed_weights(BASE_PILLAR_WEIGHTS, 0.1, rng)
        assert abs(sum(weights.values()) - 1.0) < 1e-9
        assert all(v >= 0 for v in weights.values())


def test_monte_carlo_on_empty_season_returns_zero_games():
    result = monte_carlo.run_monte_carlo_audit(2099, "sqlite:///:memory:", n_simulations=10)
    assert result.n_games == 0
    assert result.critical_failure_factor is None


@pytest.fixture()
def ingested_season(tmp_path):
    hist_url = f"sqlite:///{tmp_path}/jsa_historical_test.db"
    prod_url = f"sqlite:///{tmp_path}/jsa_prod_test.db"
    games = [
        {"game_pk": 960000 + i, "season": 2022, "game_date": f"2022-04-{10 + i:02d}",
         "home_team": "New York Yankees", "away_team": "Boston Red Sox", "home_team_id": 147, "away_team_id": 111,
         "home_pitcher_id": 1000 + i, "away_pitcher_id": 2000 + i, "is_double_header": False,
         "home_score": 5 if i % 3 else 2, "away_score": 3 if i % 3 else 6}
        for i in range(15)
    ]
    with patch("jsa.historical.pipeline.fetch_season_games", return_value=games):
        pipeline.run_season_ingestion(2022, provider=FakeProvider(), historical_database_url=hist_url, registries_database_url=prod_url)
    return hist_url


def test_monte_carlo_produces_full_structure(ingested_season):
    result = monte_carlo.run_monte_carlo_audit(2022, ingested_season, n_simulations=50, random_seed=7)
    assert result.n_games == 15
    assert result.n_simulations == 50
    assert set(result.weight_stability.keys()) == set(SEVEN_PILLARS)
    assert set(result.feature_stability.keys()) == set(SEVEN_PILLARS)
    assert 0.0 <= result.probability_collapse_rate <= 1.0
    # Con datos identicos (todos los pilares con el mismo advantage por
    # como esta armado FakeProvider), la varianza entre pilares deberia
    # ser explicable -- solo verificamos que el mecanismo corre sin
    # romperse y produce numeros no-negativos (una varianza nunca es negativa).
    assert all(v >= 0 for v in result.weight_stability.values())
    assert all(v >= 0 for v in result.feature_stability.values())


def test_monte_carlo_is_reproducible_with_same_seed(ingested_season):
    result_a = monte_carlo.run_monte_carlo_audit(2022, ingested_season, n_simulations=30, random_seed=99)
    result_b = monte_carlo.run_monte_carlo_audit(2022, ingested_season, n_simulations=30, random_seed=99)
    assert result_a.weight_stability == result_b.weight_stability
    assert result_a.critical_failure_factor == result_b.critical_failure_factor


def test_monte_carlo_never_used_to_predict_a_single_game():
    """Regla dura de la Seccion 13.2: Monte Carlo nunca debe exponer una
    prediccion por juego -- solo agregados de sensibilidad."""
    import inspect

    source = inspect.getsource(monte_carlo)
    assert "def predict" not in source
