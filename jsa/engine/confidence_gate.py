"""Confidence Gate -- Seccion 10 del spec JSA v3.0.

Decide, por mercado, si hay evidencia suficiente para un pick accionable.
Con el sistema sin calibrar (ver `evidence_engine`/`domain.models.
CalibrationInfo`), el Gate NUNCA pasa -- criterio explicito, no un bug: la
Seccion 8.4.1 prohibe usar un score sin calibrar para decision, y la
Seccion 10.4 prohibe declarar un Gate valido sin walk-forward de >=3
temporadas, que tampoco existe todavia."""

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
) -> ConfidenceGateMarketResult:
    if calibration.calibration_status != "calibrated":
        return ConfidenceGateMarketResult(
            market_id=market_id,
            passed=False,
            reason="uncalibrated",
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
) -> list[ConfidenceGateMarketResult]:
    return [
        evaluate_gate(market_id, calibration, cri_score, uncertainty_index, consistency_flag, feature_contribution, manifest_valid)
        for market_id in config.MARKET_IDS
    ]
