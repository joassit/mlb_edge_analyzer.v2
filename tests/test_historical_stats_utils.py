"""
Pruebas de las funciones estadísticas puras de historical_engine/stats_utils.py
-- valores verificados a mano, sin depender de scipy/numpy.
"""

import math

from historical_engine.stats_utils import (
    brier_score, log_loss, mae, rmse, bias, pearson_corr, spearman_corr,
    r_squared, bootstrap_ci, calibration_buckets, expected_calibration_error,
    maximum_calibration_error, sharpness,
)


def test_brier_score_perfect_predictions_is_zero():
    assert brier_score([1.0, 0.0], [1, 0]) == 0.0


def test_brier_score_always_50_50_is_quarter():
    assert brier_score([0.5, 0.5, 0.5, 0.5], [1, 0, 1, 0]) == 0.25


def test_log_loss_perfect_prediction_approaches_zero():
    result = log_loss([0.999999999999], [1])
    assert result < 0.001


def test_mae_rmse_bias_hand_verified():
    pred = [10.0, 12.0, 8.0]
    actual = [8.0, 12.0, 10.0]
    # errores: +2, 0, -2 -> MAE=(2+0+2)/3=1.333, RMSE=sqrt((4+0+4)/3)=1.633, bias=(2+0-2)/3=0
    assert abs(mae(pred, actual) - 1.3333333) < 1e-6
    assert abs(rmse(pred, actual) - 1.6329931) < 1e-6
    assert abs(bias(pred, actual) - 0.0) < 1e-9


def test_bias_detects_systematic_overprojection():
    pred = [10.0, 11.0, 9.0]
    actual = [8.0, 9.0, 7.0]  # el modelo sobreproyecta 2 carreras siempre
    assert abs(bias(pred, actual) - 2.0) < 1e-9


def test_pearson_corr_perfect_linear_relationship():
    x = [1, 2, 3, 4, 5]
    y = [2, 4, 6, 8, 10]
    assert abs(pearson_corr(x, y) - 1.0) < 1e-9


def test_pearson_corr_perfect_negative_relationship():
    x = [1, 2, 3, 4, 5]
    y = [10, 8, 6, 4, 2]
    assert abs(pearson_corr(x, y) - (-1.0)) < 1e-9


def test_spearman_corr_monotonic_but_nonlinear_is_one():
    x = [1, 2, 3, 4, 5]
    y = [1, 4, 9, 16, 25]  # no lineal, pero estrictamente creciente
    assert abs(spearman_corr(x, y) - 1.0) < 1e-9


def test_r_squared_perfect_prediction_is_one():
    pred = [1.0, 2.0, 3.0, 4.0]
    actual = [1.0, 2.0, 3.0, 4.0]
    assert abs(r_squared(pred, actual) - 1.0) < 1e-9


def test_r_squared_can_be_negative_for_bad_predictions():
    actual = [1.0, 2.0, 3.0, 4.0]
    pred = [4.0, 1.0, 4.0, 1.0]  # mucho peor que solo predecir la media
    assert r_squared(pred, actual) < 0


def test_bootstrap_ci_is_reproducible_with_same_seed():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 2.5, 3.5]
    ci_1 = bootstrap_ci(values, lambda xs: sum(xs) / len(xs), n_resamples=500, seed=42)
    ci_2 = bootstrap_ci(values, lambda xs: sum(xs) / len(xs), n_resamples=500, seed=42)
    assert ci_1 == ci_2


def test_bootstrap_ci_contains_the_point_estimate():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    point_estimate = sum(values) / len(values)
    low, high = bootstrap_ci(values, lambda xs: sum(xs) / len(xs), n_resamples=1000)
    assert low <= point_estimate <= high


def test_calibration_buckets_groups_correctly():
    confidences = [0.52, 0.58, 0.63, 0.63]
    hits = [True, False, True, True]
    buckets = calibration_buckets(confidences, hits)

    b_50_55 = next(b for b in buckets if b["label"] == "50-55%")
    b_55_60 = next(b for b in buckets if b["label"] == "55-60%")
    b_60_65 = next(b for b in buckets if b["label"] == "60-65%")

    assert b_50_55["n"] == 1 and b_50_55["hits"] == 1
    assert b_55_60["n"] == 1 and b_55_60["hits"] == 0
    assert b_60_65["n"] == 2 and b_60_65["hits"] == 2
    assert abs(b_60_65["avg_confidence"] - 0.63) < 1e-9
    assert abs(b_60_65["hit_rate"] - 1.0) < 1e-9


def test_expected_calibration_error_and_max_calibration_error():
    confidences = [0.52, 0.63, 0.63]
    hits = [False, True, True]  # 50-55%: 0/1=0% hit vs 52% conf -> gap -0.52; 60-65%: 2/2=100% vs 63% -> gap +0.37
    buckets = calibration_buckets(confidences, hits)
    ece = expected_calibration_error(buckets, total_n=3)
    mce = maximum_calibration_error(buckets)
    assert ece is not None and mce is not None
    assert mce >= ece  # MCE es el peor caso, siempre >= al promedio ponderado


def test_sharpness_higher_for_confident_predictions():
    confident = sharpness([0.9, 0.1, 0.95])
    unsure = sharpness([0.51, 0.49, 0.52])
    assert confident > unsure
