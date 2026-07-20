from jsa import config
from jsa.domain.models import CalibrationInfo, FeatureContributionEntry
from jsa.engine.confidence_gate import evaluate_all_markets, evaluate_gate

_VALIDATED_GATE_ROWS = {"gate-moneyline_home-v1": {"status": "validated_70"}}


def test_gate_never_passes_while_uncalibrated():
    calib = CalibrationInfo(calibration_status="uncalibrated", raw_probability=0.7)
    result = evaluate_gate(
        "moneyline_home", calib, cri_score=95, uncertainty_index=10, consistency_flag="aligned",
        feature_contribution=[], manifest_valid=True, gate_registry_rows=_VALIDATED_GATE_ROWS,
    )
    assert result.passed is False
    assert result.reason == "uncalibrated"


def test_all_four_markets_evaluated():
    calib = CalibrationInfo(calibration_status="uncalibrated")
    results = evaluate_all_markets(
        calib, cri_score=0, uncertainty_index=0, consistency_flag=None, feature_contribution=[], manifest_valid=True,
        gate_registry_rows={},
    )
    assert {r.market_id for r in results} == set(config.MARKET_IDS)
    assert all(r.passed is False for r in results)


def test_gate_passes_when_calibrated_and_all_criteria_met():
    calib = CalibrationInfo(calibration_status="calibrated", raw_probability=0.7, calibrated_probability=0.70)
    result = evaluate_gate(
        "moneyline_home", calib, cri_score=90, uncertainty_index=20, consistency_flag="aligned",
        feature_contribution=[], manifest_valid=True, gate_registry_rows=_VALIDATED_GATE_ROWS,
    )
    assert result.passed is True


def test_gate_fails_on_pillar_dominance_even_if_calibrated():
    calib = CalibrationInfo(calibration_status="calibrated", raw_probability=0.7, calibrated_probability=0.70)
    dominant = FeatureContributionEntry(pillar="bullpen", final_weight=0.25, advantage=2, absolute_contribution=0.5, percentage_contribution=0.9, dominance_warning=True)
    result = evaluate_gate(
        "moneyline_home", calib, cri_score=90, uncertainty_index=20, consistency_flag="aligned",
        feature_contribution=[dominant], manifest_valid=True, gate_registry_rows=_VALIDATED_GATE_ROWS,
    )
    assert result.passed is False
    assert result.criteria["no_unreviewed_pillar_dominance"] is False


def test_gate_fails_when_manifest_invalid():
    calib = CalibrationInfo(calibration_status="calibrated", raw_probability=0.7, calibrated_probability=0.70)
    result = evaluate_gate(
        "moneyline_home", calib, cri_score=90, uncertainty_index=20, consistency_flag="aligned",
        feature_contribution=[], manifest_valid=False, gate_registry_rows=_VALIDATED_GATE_ROWS,
    )
    assert result.passed is False
    assert result.criteria["manifest_valid"] is False


def test_gate_fails_when_gate_registry_not_validated_even_if_calibrated():
    """Seccion 10.4: calibracion real (1) no alcanza por si sola -- el
    Gate de este mercado tambien necesita su propio Gate Threshold Sweep
    validado (2). Sin eso, sigue sin pasar aunque todo lo demas este bien."""
    calib = CalibrationInfo(calibration_status="calibrated", raw_probability=0.7, calibrated_probability=0.70)
    result = evaluate_gate(
        "moneyline_home", calib, cri_score=90, uncertainty_index=20, consistency_flag="aligned",
        feature_contribution=[], manifest_valid=True,
        gate_registry_rows={"gate-moneyline_home-v1": {"status": "under_validation"}},
    )
    assert result.passed is False
    assert result.reason == "gate_not_validated"


def test_gate_fails_when_gate_registry_entry_missing():
    calib = CalibrationInfo(calibration_status="calibrated", raw_probability=0.7, calibrated_probability=0.70)
    result = evaluate_gate(
        "moneyline_home", calib, cri_score=90, uncertainty_index=20, consistency_flag="aligned",
        feature_contribution=[], manifest_valid=True, gate_registry_rows={},
    )
    assert result.passed is False
    assert result.reason == "gate_not_validated"
