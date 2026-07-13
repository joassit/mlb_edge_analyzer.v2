"""Decision Engine -- Seccion 11 del spec JSA v3.0. Ensambla la Final
Category. Nunca aplica la tabla de la Seccion 8.4 sobre un score sin
calibrar (Seccion 8.4.1, Instruccion Final #4) -- mientras
`calibration_status != "calibrated"`, la categoria queda bloqueada."""

from __future__ import annotations

from jsa import config
from jsa.domain.models import CalibrationInfo

UNCALIBRATED_CATEGORY = "NO_DISPONIBLE_SIN_CALIBRAR"


def compute_final_category(evidence_score_raw: float, cri_score: int, uncertainty_index: int, calibration: CalibrationInfo) -> str:
    if calibration.calibration_status != "calibrated":
        return UNCALIBRATED_CATEGORY

    if evidence_score_raw >= config.EVIDENCE_THRESHOLD_CLEAR_FAVORITE and cri_score >= config.CRI_THRESHOLD_CLEAR_FAVORITE:
        category = "Favorito Claro"
    elif evidence_score_raw >= config.EVIDENCE_THRESHOLD_MODERATE_FAVORITE:
        category = "Favorito Moderado"
    else:
        category = "Juego Equilibrado"

    if uncertainty_index > config.UNCERTAINTY_DEGRADE_CATEGORY_THRESHOLD:
        category = _degrade(category)
    return category


def _degrade(category: str) -> str:
    order = ["Favorito Claro", "Favorito Moderado", "Juego Equilibrado"]
    idx = order.index(category) if category in order else len(order) - 1
    return order[min(idx + 1, len(order) - 1)]


def one_sentence_explanation(
    home_team: str, away_team: str, evidence_score_raw: float, final_category: str, dominant_pillar: str | None
) -> str:
    if final_category == UNCALIBRATED_CATEGORY:
        return (
            f"{home_team} vs {away_team}: Evidence Score crudo {evidence_score_raw:+.2f} -- "
            f"categoria de decision no disponible porque el modelo todavia no tiene calibracion "
            f"validada (Seccion 8.4.1)."
        )
    lean = home_team if evidence_score_raw > 0 else (away_team if evidence_score_raw < 0 else "ningun equipo")
    driver = f", impulsado principalmente por {dominant_pillar}" if dominant_pillar else ""
    return f"{final_category}: evidencia inclina hacia {lean}{driver}."
