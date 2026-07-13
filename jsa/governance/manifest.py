"""RunManifest + Reglas de Invalidacion Automatica -- Secciones 14.1 y 15
del spec JSA v3.0.

Transversal desde el primer commit (Instruccion Final #7/#16): toda corrida
-- en vivo, de test de integracion, o futura de experimento -- pasa por
`build_manifest()` y `evaluate_invalidation()`. Una corrida `INVALIDATED`
no se usa para reporte, no cuenta para ningun registry, no puede pasar el
Confidence Gate (Principio 14, sin excepciones manuales)."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pydantic
import sqlalchemy

from jsa.domain.models import InvalidationReason, RunManifest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_git_commit() -> str:
    """Nunca vacio, nunca falla en silencio -- "unversioned" si el comando
    de git falla o no hay repo (Seccion 16). El caller decide si eso
    invalida la corrida (solo en contexto de produccion, ver
    `evaluate_invalidation`)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=3, cwd=_REPO_ROOT,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass
    return "unversioned"


def library_versions() -> dict[str, str]:
    return {"pydantic": pydantic.VERSION, "sqlalchemy": sqlalchemy.__version__}


def build_manifest(
    *,
    run_id: str,
    model_version: str,
    schema_version: str,
    pillar_versions: dict[str, str],
    feature_registry_version: str,
    rule_registry_version: str,
    gate_registry_version: str,
    market_registry_version: str,
    input_snapshot_hash: str,
    config_hash: str,
    random_seed: int | None = None,
) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        git_commit=get_git_commit(),
        model_version=model_version,
        schema_version=schema_version,
        pillar_versions=pillar_versions,
        feature_registry_version=feature_registry_version,
        rule_registry_version=rule_registry_version,
        gate_registry_version=gate_registry_version,
        market_registry_version=market_registry_version,
        input_snapshot_hash=input_snapshot_hash,
        output_hash=None,
        config_hash=config_hash,
        random_seed=random_seed,
        python_version=sys.version,
        library_versions=library_versions(),
    )


@dataclass
class InvalidationContext:
    """Todo lo que `evaluate_invalidation` necesita para aplicar las 12
    reglas de la Seccion 15. `is_production`: solo en contexto de
    produccion se invalida por `git_commit == "unversioned"` (regla 1) --
    un test local corriendo fuera de un repo git no deberia fallar por eso."""

    is_production: bool
    input_hash_verified: bool
    output_hash_verified: bool
    config_hash_verified: bool
    active_features_without_validation: bool = False
    active_rules_without_experiment: bool = False
    divergence_dominant_unreviewed: bool = False
    calibration_status: str = "uncalibrated"
    reliability_diagram_exists: bool = False
    miscalibrated_high_confidence_bucket: bool = False
    gate_bypassed: bool = False
    benchmark_failed: bool = False
    pillar_code_hash_mismatch: bool = False
    experimental_pillar_in_evidence_score: bool = False
    extra_warnings: list[str] = field(default_factory=list)


def evaluate_invalidation(manifest: RunManifest, ctx: InvalidationContext) -> tuple[bool, list[InvalidationReason], list[str]]:
    reasons: list[InvalidationReason] = []
    warnings: list[str] = list(ctx.extra_warnings)

    if ctx.is_production and manifest.git_commit == "unversioned":
        reasons.append("unversioned_git_commit")

    if not (ctx.input_hash_verified and ctx.output_hash_verified and ctx.config_hash_verified):
        reasons.append("hash_mismatch")

    if ctx.active_features_without_validation:
        reasons.append("active_feature_without_validation_experiment")

    if ctx.active_rules_without_experiment:
        reasons.append("active_rule_without_supporting_experiment")

    if ctx.divergence_dominant_unreviewed:
        reasons.append("divergence_flag_dominant_unreviewed")

    # Reglas 7-8 (reliability diagram / bucket miscalibrado) solo aplican
    # cuando el sistema PRETENDE estar calibrado -- mientras
    # calibration_status=="uncalibrated" el sistema ya se declara a si
    # mismo no apto para decision (calibration.py/confidence_gate.py), asi
    # que no hay una afirmacion de calibracion que pueda ser falsa. Se dejan
    # como warning visible, no como invalidacion, para que una corrida
    # limpia en esta etapa temprana del proyecto no salga siempre
    # INVALIDATED por una limitacion conocida y ya declarada.
    if ctx.calibration_status == "uncalibrated":
        warnings.append(
            "Modelo sin calibracion validada (Seccion 8.4.1) -- Final Category y Confidence Gate "
            "bloqueados por diseno, no es una falla de esta corrida especifica."
        )
    else:
        if not ctx.reliability_diagram_exists:
            reasons.append("reliability_diagram_missing")
        if ctx.miscalibrated_high_confidence_bucket:
            reasons.append("miscalibrated_high_confidence_bucket")

    if ctx.gate_bypassed:
        reasons.append("gate_bypassed")

    if ctx.benchmark_failed:
        reasons.append("benchmark_failed")

    if ctx.pillar_code_hash_mismatch:
        reasons.append("pillar_code_hash_mismatch")

    if ctx.experimental_pillar_in_evidence_score:
        reasons.append("experimental_pillar_in_evidence_score")

    invalidated = len(reasons) > 0
    return invalidated, reasons, warnings
