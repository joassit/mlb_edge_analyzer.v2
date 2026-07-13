"""Report Generator -- JSAReport v3, Seccion 11 (11.1-11.8) del spec JSA
v3.0. Punto unico de ensamblaje del reporte final -- todos los campos
obligatorios de 11.8 (integridad, hashes, manifest, weight audit, rule
trace, feature contribution, calibracion, confidence gate, warnings,
reconstruction token) se llenan aqui, nunca en el orquestador."""

from __future__ import annotations

from jsa.domain.hashing import hash_value
from jsa.domain.models import (
    CalibrationInfo,
    ConfidenceGateMarketResult,
    FeatureContributionEntry,
    GameSnapshot,
    JSAReport,
    MathAudit,
    PillarAdvantage,
    PillarWeights,
    ProjectedRunsOutput,
    RuleTraceEntry,
    RunManifest,
    WeightAuditEntry,
)


def build_report(
    *,
    run_id: str,
    snapshot: GameSnapshot,
    pillar_advantages: list[PillarAdvantage],
    weight_audit: list[WeightAuditEntry],
    evidence_score_raw: float,
    evidence_score_math_audit: MathAudit,
    cri_score: int,
    cri_math_audit: MathAudit,
    cri_effective_base: str,
    uncertainty_index: int,
    uncertainty_math_audit: MathAudit,
    base_weights: PillarWeights,
    final_weights: PillarWeights,
    rules_activated_human_readable: list[str],
    projected_runs: ProjectedRunsOutput | None,
    final_category: str,
    confidence_gate: list[ConfidenceGateMarketResult],
    one_sentence_explanation: str,
    manifest: RunManifest,
    rule_trace: list[RuleTraceEntry],
    feature_contribution: list[FeatureContributionEntry],
    calibration: CalibrationInfo,
    config_hash: str,
    warnings: list[str],
) -> JSAReport:
    manifest_status = "INVALIDATED" if manifest.invalidated else "valid"
    manifest_status_reason = ", ".join(manifest.invalidation_reasons) if manifest.invalidated else None

    report = JSAReport(
        run_id=run_id,
        game_id=snapshot.game_id,
        game_pk=snapshot.game_pk,
        game_date=snapshot.game_date,
        home_team=snapshot.home_team,
        away_team=snapshot.away_team,
        pillar_advantages=pillar_advantages,
        weight_audit=weight_audit,
        evidence_score_raw=evidence_score_raw,
        evidence_score_math_audit=evidence_score_math_audit,
        cri_score=cri_score,
        cri_math_audit=cri_math_audit,
        cri_effective_base=cri_effective_base,
        uncertainty_index=uncertainty_index,
        uncertainty_math_audit=uncertainty_math_audit,
        base_weights=base_weights,
        final_weights=final_weights,
        rules_activated_human_readable=rules_activated_human_readable,
        projected_runs=projected_runs,
        final_category=final_category,
        confidence_gate=confidence_gate,
        one_sentence_explanation=one_sentence_explanation,
        manifest_status=manifest_status,
        manifest_status_reason=manifest_status_reason,
        input_snapshot_hash=manifest.input_snapshot_hash,
        output_hash=None,
        config_hash=config_hash,
        manifest=manifest,
        rule_trace=rule_trace,
        feature_contribution=feature_contribution,
        calibration=calibration,
        monte_carlo_summary=None,  # Seccion 13.7bis -- roadmap, ver ROADMAP.md
        warnings=warnings,
        reconstruction_token="",  # se completa abajo, depende de output_hash
    )

    output_hash = report.compute_output_hash()
    report = report.model_copy(update={"output_hash": output_hash})
    reconstruction_token = hash_value({"run_id": run_id, "config_hash": config_hash, "input_snapshot_hash": manifest.input_snapshot_hash})
    report = report.model_copy(update={"reconstruction_token": reconstruction_token})
    return report
