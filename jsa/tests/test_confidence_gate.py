from jsa import config
from jsa.domain.models import CalibrationInfo, FeatureContributionEntry
from jsa.engine.confidence_gate import evaluate_all_markets, evaluate_gate


def test_gate_never_passes_while_uncalibrated():
    calib = CalibrationInfo(calibration_status="uncalibrated", raw_probability=0.7)
    result = evaluate_gate("moneyline_home", calib, cri_score=95, uncertainty_index=10, consistency_flag="aligned", feature_contribution=[], manifest_valid=True)
    assert result.passed is False
    assert result.reason == "uncalibrated"


def test_all_four_markets_evaluated():
    calib = CalibrationInfo(calibration_status="uncalibrated")
    results = evaluate_all_markets(calib, cri_score=0, uncertainty_index=0, consistency_flag=None, feature_contribution=[], manifest_valid=True)
    assert {r.market_id for r in results} == set(config.MARKET_IDS)
    assert all(r.passed is False for r in results)


def test_gate_passes_when_calibrated_and_all_criteria_met():
    calib = CalibrationInfo(calibration_status="calibrated", raw_probability=0.7, calibrated_probability=0.70)
    result = evaluate_gate(
        "moneyline_home", calib, cri_score=90, uncertainty_index=20, consistency_flag="aligned",
        feature_contribution=[], manifest_valid=True,
    )
    assert result.passed is True


def test_gate_fails_on_pillar_dominance_even_if_calibrated():
    calib = CalibrationInfo(calibration_status="calibrated", raw_probability=0.7, calibrated_probability=0.70)
    dominant = FeatureContributionEntry(pillar="bullpen", final_weight=0.25, advantage=2, absolute_contribution=0.5, percentage_contribution=0.9, dominance_warning=True)
    result = evaluate_gate(
        "moneyline_home", calib, cri_score=90, uncertainty_index=20, consistency_flag="aligned",
        feature_contribution=[dominant], manifest_valid=True,
    )
    assert result.passed is False
    assert result.criteria["no_unreviewed_pillar_dominance"] is False


def test_gate_fails_when_manifest_invalid():
    calib = CalibrationInfo(calibration_status="calibrated", raw_probability=0.7, calibrated_probability=0.70)
    result = evaluate_gate(
        "moneyline_home", calib, cri_score=90, uncertainty_index=20, consistency_flag="aligned",
        feature_contribution=[], manifest_valid=False,
    )
    assert result.passed is False
    assert result.criteria["manifest_valid"] is False
