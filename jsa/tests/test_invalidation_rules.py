"""Ejercita, una por una, las 12 reglas de invalidacion automatica de la
Seccion 15 del spec JSA v3.0."""

from __future__ import annotations

import pytest

from jsa.governance.manifest import InvalidationContext, build_manifest, evaluate_invalidation


def _base_manifest(git_commit_override: str | None = None):
    manifest = build_manifest(
        run_id="run-1", model_version="0.1.0-experimental", schema_version="3.1",
        pillar_versions={"starter": "starter@1.0.0"}, feature_registry_version="v1", rule_registry_version="v1",
        gate_registry_version="v1", market_registry_version="v1", input_snapshot_hash="abc123", config_hash="def456",
    )
    if git_commit_override is not None:
        manifest = manifest.model_copy(update={"git_commit": git_commit_override})
    return manifest


def _base_ctx(**overrides) -> InvalidationContext:
    defaults = dict(
        is_production=True, input_hash_verified=True, output_hash_verified=True, config_hash_verified=True,
    )
    defaults.update(overrides)
    return InvalidationContext(**defaults)


def test_clean_run_is_not_invalidated():
    manifest = _base_manifest()
    invalidated, reasons, warnings = evaluate_invalidation(manifest, _base_ctx())
    assert invalidated is False
    assert reasons == []
    # Uncalibrated es esperado en esta entrega -- se advierte, no invalida.
    assert any("calibracion" in w.lower() for w in warnings)


def test_rule1_unversioned_git_commit_in_production():
    manifest = _base_manifest(git_commit_override="unversioned")
    invalidated, reasons, _ = evaluate_invalidation(manifest, _base_ctx(is_production=True))
    assert invalidated is True
    assert "unversioned_git_commit" in reasons


def test_rule1_unversioned_allowed_outside_production():
    manifest = _base_manifest(git_commit_override="unversioned")
    invalidated, reasons, _ = evaluate_invalidation(manifest, _base_ctx(is_production=False))
    assert "unversioned_git_commit" not in reasons


def test_rule3_hash_mismatch():
    manifest = _base_manifest()
    invalidated, reasons, _ = evaluate_invalidation(manifest, _base_ctx(input_hash_verified=False))
    assert invalidated is True
    assert "hash_mismatch" in reasons


def test_rule4_active_feature_without_validation():
    manifest = _base_manifest()
    invalidated, reasons, _ = evaluate_invalidation(manifest, _base_ctx(active_features_without_validation=True))
    assert "active_feature_without_validation_experiment" in reasons


def test_rule5_active_rule_without_experiment():
    manifest = _base_manifest()
    invalidated, reasons, _ = evaluate_invalidation(manifest, _base_ctx(active_rules_without_experiment=True))
    assert "active_rule_without_supporting_experiment" in reasons


def test_rule6_divergence_dominant_unreviewed():
    manifest = _base_manifest()
    invalidated, reasons, _ = evaluate_invalidation(manifest, _base_ctx(divergence_dominant_unreviewed=True))
    assert "divergence_flag_dominant_unreviewed" in reasons


def test_rule7_reliability_diagram_missing_only_when_calibrated():
    manifest = _base_manifest()
    # uncalibrated -> no dispara (ver test_clean_run_is_not_invalidated)
    invalidated, reasons, _ = evaluate_invalidation(manifest, _base_ctx(calibration_status="uncalibrated", reliability_diagram_exists=False))
    assert "reliability_diagram_missing" not in reasons

    invalidated, reasons, _ = evaluate_invalidation(manifest, _base_ctx(calibration_status="calibrated", reliability_diagram_exists=False))
    assert invalidated is True
    assert "reliability_diagram_missing" in reasons


def test_rule8_miscalibrated_high_confidence_bucket():
    manifest = _base_manifest()
    invalidated, reasons, _ = evaluate_invalidation(
        manifest, _base_ctx(calibration_status="calibrated", reliability_diagram_exists=True, miscalibrated_high_confidence_bucket=True)
    )
    assert invalidated is True
    assert "miscalibrated_high_confidence_bucket" in reasons


def test_rule9_gate_bypassed():
    manifest = _base_manifest()
    invalidated, reasons, _ = evaluate_invalidation(manifest, _base_ctx(gate_bypassed=True))
    assert "gate_bypassed" in reasons


def test_rule10_benchmark_failed():
    manifest = _base_manifest()
    invalidated, reasons, _ = evaluate_invalidation(manifest, _base_ctx(benchmark_failed=True))
    assert "benchmark_failed" in reasons


def test_rule11_pillar_code_hash_mismatch():
    manifest = _base_manifest()
    invalidated, reasons, _ = evaluate_invalidation(manifest, _base_ctx(pillar_code_hash_mismatch=True))
    assert "pillar_code_hash_mismatch" in reasons


def test_rule12_experimental_pillar_in_evidence_score():
    manifest = _base_manifest()
    invalidated, reasons, _ = evaluate_invalidation(manifest, _base_ctx(experimental_pillar_in_evidence_score=True))
    assert "experimental_pillar_in_evidence_score" in reasons


def test_rule2_manifest_absent_is_impossible_by_construction():
    """Regla 2 (manifest ausente) se hace cumplir estructuralmente: un
    JSAReport exige un RunManifest no-opcional (Pydantic), asi que no
    existe forma de construir un reporte valido sin manifest."""
    from jsa.domain.models import JSAReport

    assert "manifest" in JSAReport.model_fields
    assert JSAReport.model_fields["manifest"].is_required()
