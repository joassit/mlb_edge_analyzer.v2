"""Punto UNICO de evaluacion de un juego -- la funcion pura que la Seccion
2 del spec exige como frontera entre "detectar/pesar/evaluar" (este
modulo) y "hacer I/O" (`jsa/main.py`). Recibe un `GameSnapshot` ya
congelado y el estado ya leido de los registries; nunca golpea una API, un
reloj de pared (salvo para el timestamp del manifest) ni la base de datos.

Esto es lo que garantiza que "evaluar en vivo" y un futuro "recalcular
desde un snapshot historico" (backtest, Fase 3-5 del roadmap) usen
exactamente la misma logica -- la leccion mas repetida de
`mlb_edge_analyzer.v2` (su `model/predictor.py` es el precedente directo)."""

from __future__ import annotations

import numpy as np

from jsa.domain.hashing import hash_value
from jsa.domain.models import SEVEN_PILLARS, CalibrationInfo, GameSnapshot, JSAReport, PillarWeights
from jsa.engine import confidence_gate, decision_engine
from jsa.engine.context_detector import detect_context
from jsa.engine.evidence_engine import (
    apply_consistency_penalty,
    compute_cri,
    compute_evidence_score,
    compute_feature_contribution,
    compute_uncertainty_index,
)
from jsa.engine.pillars import evaluate_all_pillars
from jsa.engine.pillars.base import PILLAR_CONTRACT_VERSIONS
from jsa.engine.projected_runs import compute_consistency_flag, compute_projected_runs
from jsa.engine.rule_engine import accumulate_deltas, evaluate_rules, human_readable_summary, rules_applied_per_pillar
from jsa.engine.weight_engine import apply_weights
from jsa.governance.manifest import InvalidationContext, build_manifest, evaluate_invalidation
from jsa.reporting.report_builder import build_report


def _build_calibration_info(evidence_score_raw: float, calibration_registry_rows: dict[str, dict]) -> CalibrationInfo:
    """Seccion 8.4.1 -- unica fuente permitida de "calibrado": una curva
    isotonica ya ajustada y validada leave-one-season-out
    (`historical/calibration.py`), persistida en `calibration_registry`
    con `status="validated"` bajo `config.PRODUCTION_CALIBRATION_ID`.
    `raw_probability` se deriva de `evidence_score_raw` -- reemplaza el
    valor Skellam-derivado que se usaba antes de que el Evidence Engine
    tuviera su propia curva de calibracion (ver ROADMAP.md)."""
    from jsa.config import PRODUCTION_CALIBRATION_ID

    entry = calibration_registry_rows.get(PRODUCTION_CALIBRATION_ID)
    if entry is None or entry.get("status") != "validated":
        return CalibrationInfo(calibration_status="uncalibrated", raw_probability=evidence_score_raw, calibrated_probability=None)

    x_knots, y_knots = entry.get("x_knots") or [], entry.get("y_knots") or []
    if not x_knots or not y_knots:
        return CalibrationInfo(calibration_status="uncalibrated", raw_probability=evidence_score_raw, calibrated_probability=None)

    calibrated_probability = float(np.interp(evidence_score_raw, x_knots, y_knots))
    return CalibrationInfo(
        calibration_status="calibrated", raw_probability=evidence_score_raw, calibrated_probability=calibrated_probability,
    )


def compute_config_hash(base_weights: dict[str, float]) -> str:
    """Hash de los pesos base + umbrales de decision -- Seccion 14.1
    `config_hash`. Recalculado siempre desde `config.py` (nunca confiado
    ciegamente a un valor pasado por el caller) para que la verificacion
    independiente de la Seccion 15 regla 3 sea real."""
    from jsa import config as cfg

    payload = {
        "base_weights": base_weights,
        "cri_components": cfg.CRI_COMPONENTS,
        "uncertainty": {
            "base": cfg.UNCERTAINTY_BASE,
            "bullpen_fatigue": cfg.UNCERTAINTY_BULLPEN_FATIGUE,
            "extreme_weather": cfg.UNCERTAINTY_EXTREME_WEATHER,
            "double_header": cfg.UNCERTAINTY_DOUBLE_HEADER,
            "extreme_travel": cfg.UNCERTAINTY_EXTREME_TRAVEL,
            "per_injury": cfg.UNCERTAINTY_PER_INJURY,
        },
        "gate": {
            "p_min": cfg.GATE_P_MIN, "cri_min": cfg.GATE_CRI_MIN, "uncertainty_max": cfg.GATE_UNCERTAINTY_MAX,
            "dominance_threshold": cfg.GATE_DOMINANCE_THRESHOLD,
        },
        "evidence_thresholds": {
            "clear_favorite": cfg.EVIDENCE_THRESHOLD_CLEAR_FAVORITE, "moderate_favorite": cfg.EVIDENCE_THRESHOLD_MODERATE_FAVORITE,
        },
    }
    return hash_value(payload)


def evaluate_game(
    snapshot: GameSnapshot,
    *,
    run_id: str,
    model_version: str,
    rule_registry_rows: dict[str, dict],
    feature_registry_rows: dict[str, dict],
    pillar_registry_rows: dict[str, dict],
    calibration_registry_rows: dict[str, dict],
    gate_registry_rows: dict[str, dict],
    registry_version_tag: str,
    experiment_ids: set[str],
    is_production: bool,
) -> JSAReport:
    from jsa.config import BASE_PILLAR_WEIGHTS
    from jsa.domain.models import RuleDefinition

    context = detect_context(snapshot)
    pillars = evaluate_all_pillars(snapshot, context)

    rule_defs = {
        rid: RuleDefinition(
            rule_id=row["rule_id"], trigger=row["trigger"], condition=row["condition"],
            weight_adjustments=row["weight_adjustments"], scientific_justification=row["scientific_justification"],
            version=row["version"], status=row["status"], experiments_supporting_rule=row["experiments_supporting_rule"],
            trace_link=row.get("trace_link"),
        )
        for rid, row in rule_registry_rows.items()
    }
    trace = evaluate_rules(snapshot, context, rule_defs, experiment_ids)
    deltas = accumulate_deltas(trace)
    rap = rules_applied_per_pillar(trace)
    final_weights, weight_audit = apply_weights(BASE_PILLAR_WEIGHTS, deltas, rap)
    base_weights_model = PillarWeights(**BASE_PILLAR_WEIGHTS)

    evidence_score, ev_audit = compute_evidence_score(pillars, final_weights)
    feature_contribution = compute_feature_contribution(pillars, final_weights)
    cri, cri_audit, cri_base = compute_cri(snapshot)
    uncertainty, unc_audit = compute_uncertainty_index(snapshot, context)

    projected = compute_projected_runs(snapshot)
    consistency_flag = compute_consistency_flag(evidence_score, projected.projected_margin)
    projected = projected.model_copy(update={"consistency_flag": consistency_flag})
    cri = apply_consistency_penalty(cri, consistency_flag)

    calibration = _build_calibration_info(evidence_score, calibration_registry_rows)

    final_category = decision_engine.compute_final_category(evidence_score, cri, uncertainty, calibration)
    dominant = max(feature_contribution, key=lambda f: f.percentage_contribution, default=None)
    explanation = decision_engine.one_sentence_explanation(
        snapshot.home_team, snapshot.away_team, evidence_score, final_category, dominant.pillar if dominant else None
    )

    config_hash = compute_config_hash(BASE_PILLAR_WEIGHTS)
    manifest = build_manifest(
        run_id=run_id, model_version=model_version, schema_version=snapshot.schema_version,
        pillar_versions=dict(PILLAR_CONTRACT_VERSIONS), feature_registry_version=registry_version_tag,
        rule_registry_version=registry_version_tag, gate_registry_version=registry_version_tag,
        market_registry_version=registry_version_tag, input_snapshot_hash=snapshot.snapshot_hash, config_hash=config_hash,
    )

    gates = confidence_gate.evaluate_all_markets(
        calibration, cri, uncertainty, consistency_flag, feature_contribution, manifest_valid=True,
        gate_registry_rows=gate_registry_rows,
    )

    active_features_without_validation = any(
        row["status"] == "active" and not row.get("validation_experiment") for row in feature_registry_rows.values()
    )
    active_rules_without_experiment = any(
        row["status"] == "active" and not row.get("experiments_supporting_rule") for row in rule_registry_rows.values()
    )
    divergence_dominant_unreviewed = any(row.get("divergence_flag") for row in feature_registry_rows.values())
    pillar_code_hash_mismatch = any(
        pillar_registry_rows.get(p, {}).get("contract_version") != PILLAR_CONTRACT_VERSIONS[p] for p in SEVEN_PILLARS
    )
    experimental_pillar_in_evidence_score = any(pillar_registry_rows.get(p, {}).get("status") != "active" for p in SEVEN_PILLARS)
    gate_bypassed = any(g.passed and not all(g.criteria.values()) for g in gates)

    input_hash_verified = snapshot.compute_hash() == snapshot.snapshot_hash
    config_hash_verified = config_hash == manifest.config_hash

    invalidation_ctx = InvalidationContext(
        is_production=is_production,
        input_hash_verified=input_hash_verified,
        output_hash_verified=True,  # se reconfirma abajo, tras construir el reporte
        config_hash_verified=config_hash_verified,
        active_features_without_validation=active_features_without_validation,
        active_rules_without_experiment=active_rules_without_experiment,
        divergence_dominant_unreviewed=divergence_dominant_unreviewed,
        calibration_status=calibration.calibration_status,
        gate_bypassed=gate_bypassed,
        pillar_code_hash_mismatch=pillar_code_hash_mismatch,
        experimental_pillar_in_evidence_score=experimental_pillar_in_evidence_score,
    )
    invalidated, reasons, warnings = evaluate_invalidation(manifest, invalidation_ctx)
    manifest = manifest.model_copy(update={"invalidated": invalidated, "invalidation_reasons": reasons, "warnings": warnings})

    report = build_report(
        run_id=run_id, snapshot=snapshot, pillar_advantages=pillars, weight_audit=weight_audit,
        evidence_score_raw=evidence_score, evidence_score_math_audit=ev_audit, cri_score=cri, cri_math_audit=cri_audit,
        cri_effective_base=cri_base, uncertainty_index=uncertainty, uncertainty_math_audit=unc_audit,
        base_weights=base_weights_model, final_weights=final_weights, rules_activated_human_readable=human_readable_summary(trace),
        projected_runs=projected, final_category=final_category, confidence_gate=gates, one_sentence_explanation=explanation,
        manifest=manifest, rule_trace=trace, feature_contribution=feature_contribution, calibration=calibration,
        config_hash=config_hash, warnings=list(warnings),
    )

    # Verificacion independiente real de output_hash (Seccion 15 regla 3):
    # recalcula desde el reporte YA construido y compara contra el hash
    # que quedo grabado -- si alguna vez difieren (bug futuro en
    # report_builder), esta corrida debe salir INVALIDATED, no reportarse
    # como valida con un hash que no verifica.
    if report.compute_output_hash() != report.output_hash:
        manifest = manifest.model_copy(
            update={"invalidated": True, "invalidation_reasons": list(set(manifest.invalidation_reasons + ["hash_mismatch"]))}
        )
        report = report.model_copy(update={"manifest": manifest, "manifest_status": "INVALIDATED", "manifest_status_reason": "hash_mismatch"})

    return report
