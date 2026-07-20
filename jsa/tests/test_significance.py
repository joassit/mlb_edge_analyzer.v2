"""`historical/significance.py` -- Seccion 12.8: bootstrap + McNemar +
permutacion, compartidas por todos los candidate audits. Datos
sinteticos deterministicos, nunca red."""

from __future__ import annotations

import random

from jsa.historical import significance as sig


def _identical_pairs(n: int, seed: int = 0) -> list[tuple[float, int]]:
    rng = random.Random(seed)
    return [(rng.random(), rng.choice([0, 1])) for _ in range(n)]


def _clearly_better_alt_pairs(baseline_pairs: list[tuple[float, int]]) -> list[tuple[float, int]]:
    """Alt predice CASI perfectamente el resultado real -- Brier mucho
    mejor que baseline (que es ruido puro)."""
    return [(0.95 if y == 1 else 0.05, y) for _, y in baseline_pairs]


def _clearly_worse_alt_pairs(baseline_pairs: list[tuple[float, int]]) -> list[tuple[float, int]]:
    """Alt predice CASI perfectamente lo CONTRARIO del resultado real."""
    return [(0.05 if y == 1 else 0.95, y) for _, y in baseline_pairs]


def test_paired_bootstrap_ci_identical_predictions_not_significant():
    baseline = _identical_pairs(200)
    result = sig.paired_bootstrap_ci(baseline, list(baseline))
    assert result["delta_brier_mean"] == 0.0
    assert result["significant"] is False


def test_paired_bootstrap_ci_detects_real_improvement():
    baseline = _identical_pairs(300)
    alt = _clearly_better_alt_pairs(baseline)
    result = sig.paired_bootstrap_ci(baseline, alt)
    assert result["delta_brier_mean"] < 0
    assert result["significant"] is True


def test_paired_bootstrap_ci_none_on_length_mismatch():
    assert sig.paired_bootstrap_ci([], []) is None
    assert sig.paired_bootstrap_ci([(0.5, 1)], [(0.5, 1), (0.4, 0)]) is None


def test_mcnemar_test_no_discordant_pairs_is_not_significant():
    baseline = _identical_pairs(150)
    result = sig.mcnemar_test(baseline, list(baseline))
    assert result["b"] == 0 and result["c"] == 0
    assert result["significant"] is False
    assert result["alt_better"] is False


def test_mcnemar_test_detects_real_improvement():
    baseline = _identical_pairs(300)
    alt = _clearly_better_alt_pairs(baseline)
    result = sig.mcnemar_test(baseline, alt)
    assert result["c"] > result["b"]
    assert result["alt_better"] is True
    assert result["significant"] is True


def test_mcnemar_test_detects_real_deterioration():
    baseline = _identical_pairs(300)
    alt = _clearly_worse_alt_pairs(baseline)
    result = sig.mcnemar_test(baseline, alt)
    assert result["b"] > result["c"]
    assert result["alt_better"] is False
    assert result["significant"] is True


def test_permutation_test_identical_predictions_not_significant():
    baseline = _identical_pairs(200)
    result = sig.permutation_test_delta_brier(baseline, list(baseline))
    assert result["observed_delta_brier_mean"] == 0.0
    assert result["significant"] is False


def test_permutation_test_detects_real_improvement():
    baseline = _identical_pairs(300)
    alt = _clearly_better_alt_pairs(baseline)
    result = sig.permutation_test_delta_brier(baseline, alt)
    assert result["observed_delta_brier_mean"] < 0
    assert result["significant"] is True


def test_full_significance_report_passes_all_three_when_alt_clearly_better():
    baseline = _identical_pairs(400)
    alt = _clearly_better_alt_pairs(baseline)
    report = sig.full_significance_report(baseline, alt)
    assert report["improves"] is True
    assert report["effect_size_ok"] is True
    assert report["bootstrap"]["significant"] is True
    assert report["mcnemar"]["significant"] is True
    assert report["mcnemar"]["alt_better"] is True
    assert report["permutation"]["significant"] is True
    assert report["passes_all_three"] is True


def test_full_significance_report_fails_when_alt_worse():
    baseline = _identical_pairs(400)
    alt = _clearly_worse_alt_pairs(baseline)
    report = sig.full_significance_report(baseline, alt)
    assert report["improves"] is False
    assert report["passes_all_three"] is False


def test_full_significance_report_fails_when_no_real_difference():
    baseline = _identical_pairs(300)
    report = sig.full_significance_report(baseline, list(baseline))
    assert report["passes_all_three"] is False
