"""
Comparación completa entre los 3 motores de probabilidad
(heurístico/skellam/negbin) + el Historical Confidence Engine -- nunca
selecciona un "ganador" automático. Devuelve una tabla de métricas lado a
lado y una lista de observaciones basadas en los datos ("bajo qué
condiciones") para que la decisión la tome una persona.
"""

from historical_engine.validation import validate_source, SOURCES


def compare_models(season_year: int, run_id: int, session_factory=None) -> dict:
    """
    Tabla comparativa {source: metrics} para heuristic/skellam/negbin
    (recalculando via validate_source, no re-implementando la lógica), más
    una entrada separada para historical_confidence_engine que documenta
    explícitamente que no es comparable en las mismas columnas (no genera
    probabilidades de resultado).
    """
    table = {source: validate_source(season_year, source, run_id, session_factory) for source in SOURCES}

    table["historical_confidence_engine"] = {
        "n_sample": None, "accuracy": None, "brier_score": None, "log_loss": None,
        "ece": None, "mce": None, "sharpness": None,
        "note": (
            "No comparable en estas columnas: el Historical Confidence Engine no genera "
            "probabilidades de resultado, así que no tiene accuracy/Brier/log-loss propios -- "
            "ver historical_engine/confidence_engine.py. Se evalúa por separado en términos de "
            "cobertura de evidencia (evidence_level) y robustez, no de acierto."
        ),
    }

    observations = _build_observations(table)
    return {"season_year": season_year, "table": table, "observations": observations}


def _build_observations(table: dict) -> list[str]:
    """Genera observaciones basadas ÚNICAMENTE en los números ya calculados
    -- nunca declara un ganador único, describe condiciones ('bajo qué
    circunstancias cada motor se comporta mejor')."""
    observations = []
    numeric = {k: v for k, v in table.items() if v.get("accuracy") is not None}
    if not numeric:
        return ["Sin datos suficientes en ningún motor para comparar todavía."]

    best_accuracy = max(numeric.items(), key=lambda kv: kv[1]["accuracy"])
    observations.append(
        f"Mayor accuracy: {best_accuracy[0]} ({best_accuracy[1]['accuracy']:.1%}, "
        f"n={best_accuracy[1]['n_sample']}) -- no implica mejor calibración de probabilidad, ver Brier/ECE."
    )

    brier_candidates = {k: v for k, v in numeric.items() if v.get("brier_score") is not None}
    if brier_candidates:
        best_brier = min(brier_candidates.items(), key=lambda kv: kv[1]["brier_score"])
        observations.append(
            f"Menor Brier score (mejor calibración cruda): {best_brier[0]} ({best_brier[1]['brier_score']:.4f})."
        )

    ece_candidates = {k: v for k, v in numeric.items() if v.get("ece") is not None}
    if ece_candidates:
        best_ece = min(ece_candidates.items(), key=lambda kv: kv[1]["ece"])
        observations.append(
            f"Menor ECE (confianza declarada más alineada con acierto real por bucket): "
            f"{best_ece[0]} ({best_ece[1]['ece']:.1%})."
        )

    low_n = [k for k, v in numeric.items() if (v.get("n_sample") or 0) < 200]
    if low_n:
        observations.append(
            f"Motores con muestra por debajo del umbral de 200 (config.MIN_SAMPLE_FOR_VALIDATION): "
            f"{', '.join(low_n)} -- cualquier comparación entre ellos es preliminar."
        )

    return observations
