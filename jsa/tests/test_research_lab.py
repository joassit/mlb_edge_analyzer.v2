"""`research_lab/` -- Game Flow Research Lab (2026-07-21): baseline real
leido de los registries, plantilla de reporte de hipotesis, y criterio de
permanencia. Todo contra SQLite real en memoria, nunca mockeado."""

from __future__ import annotations

from jsa.registries import db as registries_db
from jsa.research_lab import baseline
from jsa.research_lab.hypothesis_report import HypothesisReport, decide_retention, evaluate_brier_significance
from jsa.research_lab.registry_sync import append_hypothesis_result


def _fresh_engine():
    engine = registries_db.get_engine("sqlite:///:memory:")
    registries_db.init_registries(engine)
    return engine


def test_load_gate_baseline_empty_when_nothing_synced():
    engine = _fresh_engine()
    assert baseline.load_gate_baseline(engine) == {}


def test_load_gate_baseline_reads_real_rows():
    engine = _fresh_engine()
    registries_db.append(
        engine, registries_db.gate_registry,
        gate_id="gate-moneyline_home-v1", market="moneyline_home", p_min=0.6, cri_min=16, uncertainty_max=50,
        accuracy_wilson_ci_low=0.5856, accuracy_wilson_ci_high=0.6723, coverage_pct=3.61, coverage_n=473,
        status="validated_below_70", validation_seasons=[2022, 2023, 2024, 2025, 2026], manifest_hash=None,
    )
    result = baseline.load_gate_baseline(engine)
    assert set(result) == {"moneyline_home"}
    assert result["moneyline_home"]["status"] == "validated_below_70"
    assert result["moneyline_home"]["coverage_n"] == 473


def test_load_gate_baseline_uses_latest_row_when_multiple_exist():
    """Append-only -- una fila nueva con el mismo gate_id reemplaza cual
    es "la" version vigente para el baseline, sin borrar la anterior."""
    engine = _fresh_engine()
    registries_db.append(
        engine, registries_db.gate_registry,
        gate_id="gate-moneyline_home-v1", market="moneyline_home", p_min=0.5, cri_min=10, uncertainty_max=40,
        accuracy_wilson_ci_low=0.5, accuracy_wilson_ci_high=0.6, coverage_pct=1.0, coverage_n=10,
        status="rejected_insufficient_data", validation_seasons=[], manifest_hash=None,
    )
    registries_db.append(
        engine, registries_db.gate_registry,
        gate_id="gate-moneyline_home-v1", market="moneyline_home", p_min=0.6, cri_min=16, uncertainty_max=50,
        accuracy_wilson_ci_low=0.5856, accuracy_wilson_ci_high=0.6723, coverage_pct=3.61, coverage_n=473,
        status="validated_below_70", validation_seasons=[2022, 2023, 2024, 2025, 2026], manifest_hash=None,
    )
    result = baseline.load_gate_baseline(engine)
    assert result["moneyline_home"]["status"] == "validated_below_70"
    assert result["moneyline_home"]["coverage_n"] == 473


def test_load_calibration_baseline_none_when_nothing_synced():
    engine = _fresh_engine()
    assert baseline.load_calibration_baseline(engine) is None


def test_load_calibration_baseline_reads_production_curve():
    from jsa.config import PRODUCTION_CALIBRATION_ID

    engine = _fresh_engine()
    registries_db.append(
        engine, registries_db.calibration_registry,
        calibration_id=PRODUCTION_CALIBRATION_ID, market="moneyline_home", source_field="evidence_score_raw",
        method="isotonic_regression", x_knots=[-1.0, 1.0], y_knots=[0.3, 0.7], x_min=-1.0, x_max=1.0,
        n_games_fitted=13101, seasons_used=[2022, 2023, 2024, 2025, 2026],
        loso_seasons_validated=[2022, 2023, 2024, 2025, 2026], loso_n_games=13101,
        loso_brier=0.2450574073526572, loso_log_loss=0.6831596807567893, loso_accuracy=0.5534692008243646,
        loso_ece=0.0020263721118798936, loso_mce=0.05328882810811092, status="validated", date="2026-07-20",
    )
    result = baseline.load_calibration_baseline(engine)
    assert result is not None
    assert result["loso_brier"] == 0.2450574073526572
    assert result["status"] == "validated"


def test_load_full_baseline_combines_both_sources():
    engine = _fresh_engine()
    result = baseline.load_full_baseline(engine)
    assert result == {"gate": {}, "calibration": None}


def test_decide_retention_true_when_one_metric_significant():
    significance = {
        "brier": {"significant": True, "delta_brier_mean": -0.01},
        "accuracy": {"significant": False},
    }
    retained, reason = decide_retention(significance)
    assert retained is True
    assert "brier" in reason


def test_decide_retention_false_when_nothing_significant():
    significance = {"brier": {"significant": False}, "accuracy": None}
    retained, reason = decide_retention(significance)
    assert retained is False
    assert "Ninguna" in reason


def test_evaluate_brier_significance_reuses_significance_module():
    baseline_pairs = [(0.5, 1), (0.5, 0)] * 50
    hypothesis_pairs = [(0.9, 1), (0.1, 0)] * 50  # mucho mejor -- error cuadratico bajisimo
    result = evaluate_brier_significance(baseline_pairs, hypothesis_pairs)
    assert result is not None
    assert result["significant"] is True
    assert result["delta_brier_mean"] < 0  # hypothesis mejora sobre baseline


def test_append_hypothesis_result_writes_experiment_registry_never_gate_or_calibration():
    engine = _fresh_engine()
    report = HypothesisReport(
        hypothesis_id="gflab-closer_leverage-v1", module_name="Closer Leverage Engine", market="moneyline_home",
        n_games=1000, delta_brier=-0.01, retained_in_lab=True, retention_reason="Mejora estadisticamente consistente en: brier",
        significance={"brier": {"significant": True}},
    )
    append_hypothesis_result(engine, report, seasons=[2022, 2023, 2024, 2025, 2026])

    experiments = registries_db.latest_by_id(engine, registries_db.experiment_registry, "experiment_id")
    assert "gflab-closer_leverage-v1" in experiments
    assert experiments["gflab-closer_leverage-v1"]["decision"] == "retained_in_lab"
    assert experiments["gflab-closer_leverage-v1"]["benchmarking_result"]["delta_brier"] == -0.01
    # Nunca escribe en gate_registry ni calibration_registry -- solo experiment_registry es evidencia,
    # nunca configuracion activa.
    assert registries_db.all_rows(engine, registries_db.gate_registry) == []
    assert registries_db.all_rows(engine, registries_db.calibration_registry) == []


def test_append_hypothesis_result_rejected_decision_when_not_retained():
    engine = _fresh_engine()
    report = HypothesisReport(
        hypothesis_id="gflab-closer_leverage-v1", module_name="Closer Leverage Engine", market="moneyline_home",
        n_games=1000, retained_in_lab=False, retention_reason="Ninguna metrica mostro mejora estadisticamente consistente sobre el baseline",
    )
    append_hypothesis_result(engine, report, seasons=[2022])
    experiments = registries_db.latest_by_id(engine, registries_db.experiment_registry, "experiment_id")
    assert experiments["gflab-closer_leverage-v1"]["decision"] == "rejected_no_improvement"
