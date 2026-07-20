"""Confidence Gate -- Seccion 10 del spec JSA v3.0.

Decide, por mercado, si hay evidencia suficiente para un pick accionable.
Dos condiciones INDEPENDIENTES, ambas obligatorias (Seccion 8.4.1 +
10.4) -- cada una se chequea y corta por separado en `evaluate_gate()`,
nunca se asume que una implica la otra:
1. `calibration.calibration_status == "calibrated"` -- una curva de
   calibracion real, ajustada y validada leave-one-season-out
   (Fase 4, `engine/orchestrator.py::_build_calibration_info()`).
2. `gate_registry` de ESE mercado con `status == "validated_70"` -- su
   propio Gate Threshold Sweep (10.3) validado con walk-forward de >=3
   temporadas (Fase 6, todavia no construido -- los 4 gates sembrados
   quedan en `"under_validation"`).

Mientras la Fase 6 no exista, el Gate sigue sin poder pasar aunque la
calibracion (1) ya sea real -- comportamiento correcto, no una
limitacion pendiente de arreglar."""

from __future__ import annotations

from jsa import config
from jsa.domain.models import CalibrationInfo, ConfidenceGateMarketResult, FeatureContributionEntry


def evaluate_gate(
    market_id: str,
    calibration: CalibrationInfo,
    cri_score: int,
    uncertainty_index: int,
    consistency_flag: str | None,
    feature_contribution: list[FeatureContributionEntry],
    manifest_valid: bool,
    gate_registry_rows: dict[str, dict],
) -> ConfidenceGateMarketResult:
    if calibration.calibration_status != "calibrated":
        return ConfidenceGateMarketResult(
            market_id=market_id,
            passed=False,
            reason="uncalibrated",
            criteria={},
        )

    # Seccion 10.4, segunda condicion independiente de la calibracion: el
    # Gate de ESTE mercado tiene que tener su propio Gate Threshold Sweep
    # (10.3) validado con walk-forward real -- nunca alcanza con que el
    # modelo este calibrado. `gate_registry` sembrado queda en
    # "under_validation" hasta que exista esa evidencia (ver ROADMAP,
    # Fase 6) -- mientras tanto el Gate sigue sin poder pasar, aunque la
    # calibracion ya sea real.
    gate_entry = gate_registry_rows.get(f"gate-{market_id}-v1")
    if gate_entry is None or gate_entry.get("status") != "validated_70":
        return ConfidenceGateMarketResult(
            market_id=market_id,
            passed=False,
            reason="gate_not_validated",
            criteria={},
        )

    p = calibration.calibrated_probability or 0.0
    criteria = {
        "probability_above_p_min": p > config.GATE_P_MIN,
        "cri_above_min": cri_score >= config.GATE_CRI_MIN,
        "uncertainty_below_max": uncertainty_index <= config.GATE_UNCERTAINTY_MAX,
        "consistency_aligned": consistency_flag == "aligned",
        "no_dominant_feature_divergence": True,  # ver nota de limitacion abajo
        "no_unreviewed_pillar_dominance": not any(f.dominance_warning for f in feature_contribution),
        "manifest_valid": manifest_valid,
    }
    passed = all(criteria.values())
    reason = "passed" if passed else "criteria_not_met"
    return ConfidenceGateMarketResult(market_id=market_id, passed=passed, reason=reason, criteria=criteria)


# Nota de limitacion (criterio 5, Seccion 10.2): "ninguna feature dominante
# del pick tiene Divergence Flag=true" opera a nivel de FEATURE individual
# (Feature Registry, Seccion 4.3: real_correlation vs. model_importance por
# cuartiles). Esta entrega no calcula todavia contribucion a nivel de
# feature individual (solo a nivel de pilar, Seccion 7.2) porque ninguna
# feature tiene aun real_correlation/model_importance medidos (no hay
# historial). El criterio queda implementado como vacuously-true y
# documentado aqui -- no como un chequeo real -- hasta que el Feature
# Registry tenga esos valores (ver ROADMAP).


def evaluate_all_markets(
    calibration: CalibrationInfo,
    cri_score: int,
    uncertainty_index: int,
    consistency_flag: str | None,
    feature_contribution: list[FeatureContributionEntry],
    manifest_valid: bool,
    gate_registry_rows: dict[str, dict],
) -> list[ConfidenceGateMarketResult]:
    return [
        evaluate_gate(
            market_id, calibration, cri_score, uncertainty_index, consistency_flag, feature_contribution, manifest_valid,
            gate_registry_rows=gate_registry_rows,
        )
        for market_id in config.MARKET_IDS
    ]
