"""Paridad numerica de los modelos legado portados -- ver jsa/legacy/README.md."""

from __future__ import annotations

import math

from jsa.legacy import heuristic_model, negbin_model, skellam_model
from jsa.legacy.calibration_constants import NEGBIN_DISPERSION, SKELLAM_SHRINKAGE_ALPHA, calibrated_skellam_win_prob


def test_heuristic_pitcher_score_caps_at_era_six():
    assert heuristic_model.pitcher_score(6.0) == 0.0
    assert heuristic_model.pitcher_score(10.0) == 0.0
    assert heuristic_model.pitcher_score(0.0) == 1.0


def test_heuristic_model_prob_bounded():
    p = heuristic_model.model_prob(p_era=3.0, team_ops=0.800, bullpen_era=3.5)
    assert 0.05 <= p <= 0.95


def test_heuristic_normalize_matchup_sums_to_one():
    away, home = heuristic_model.normalize_matchup(0.5, 0.5, home_field_adv=0.02)
    assert abs(away + home) - 1.0 < 1e-9


def test_skellam_win_prob_symmetric_when_mus_equal():
    p = skellam_model.skellam_win_prob(4.5, 4.5)
    assert abs(p - 0.5) < 1e-6


def test_skellam_win_prob_favors_higher_mu():
    assert skellam_model.skellam_win_prob(6.0, 3.0) > 0.5


def test_calibrated_skellam_shrinks_toward_half():
    raw = skellam_model.skellam_win_prob(6.0, 3.0)
    calibrated = calibrated_skellam_win_prob(6.0, 3.0)
    assert calibrated == 0.5 + SKELLAM_SHRINKAGE_ALPHA * (raw - 0.5)
    # La version calibrada esta mas cerca de 0.5 que la cruda (menos confiada).
    assert abs(calibrated - 0.5) < abs(raw - 0.5)


def test_negbin_win_prob_converges_to_skellam_as_k_grows():
    """Poisson es el caso limite k -> infinito del Binomial Negativo --
    misma prueba de convergencia que
    mlb_edge_analyzer.v2/tests/test_negbin_model.py."""
    skellam_p = skellam_model.skellam_win_prob(5.0, 4.0)
    negbin_p_large_k = negbin_model.negbin_win_prob(5.0, 4.0, k=100000.0)
    assert math.isclose(skellam_p, negbin_p_large_k, abs_tol=1e-3)


def test_negbin_win_prob_at_calibrated_dispersion_is_bounded():
    p = negbin_model.negbin_win_prob(5.0, 4.0, k=NEGBIN_DISPERSION)
    assert 0.0 <= p <= 1.0


def test_negbin_run_line_prob_sums_to_one():
    home, away = negbin_model.negbin_run_line_prob(5.0, 4.0, k=NEGBIN_DISPERSION, line=1.5)
    assert abs((home + away) - 1.0) < 1e-9


def test_negbin_totals_prob_sums_to_one_for_half_line():
    over, under = negbin_model.negbin_totals_prob(5.0, 4.0, k=NEGBIN_DISPERSION, line=8.5)
    assert abs((over + under) - 1.0) < 1e-9


def test_negbin_run_line_rejects_invalid_favorite_side():
    import pytest

    with pytest.raises(ValueError):
        negbin_model.negbin_run_line_prob(5.0, 4.0, k=NEGBIN_DISPERSION, favorite_side="neither")
