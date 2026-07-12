"""
Validación estadística del histórico -- calcula Brier, Log Loss,
calibración/ECE/MCE, sharpness, bootstrap CI, y drift entre temporadas,
por motor (heuristic/skellam/negbin), y persiste en HistoricalMetrics/
HistoricalCalibration.

Todo el cálculo numérico vive en stats_utils.py (puro, sin DB) -- este
módulo solo hace la orquestación de leer HistoricalPrediction/
HistoricalAnalysis y escribir HistoricalMetrics/HistoricalCalibration.
"""

import json

from historical_engine.db import (
    HistoricalPrediction, HistoricalMetrics, HistoricalCalibration, SessionLocal,
)
from historical_engine.stats_utils import (
    brier_score, log_loss, calibration_buckets, expected_calibration_error,
    maximum_calibration_error, sharpness, bootstrap_ci,
)

SOURCES = ("heuristic", "skellam", "negbin")


def _confidences_and_hits(predictions: list) -> tuple[list[float], list[bool]]:
    confidences, hits = [], []
    for p in predictions:
        if p.correct is None:
            continue  # sin resultado todavía -- no cuenta para validación
        prob_for_side = p.home_prob if p.predicted_winner == "home" else p.away_prob
        confidences.append(max(prob_for_side, 1 - prob_for_side))
        hits.append(bool(p.correct))
    return confidences, hits


def _home_probs_and_outcomes(predictions: list) -> tuple[list[float], list[int]]:
    probs, outcomes = [], []
    for p in predictions:
        if p.actual_winner is None:
            continue
        probs.append(p.home_prob)
        outcomes.append(1 if p.actual_winner == "home" else 0)
    return probs, outcomes


def validate_source(season_year: int, source: str, run_id: int, session_factory=None) -> dict:
    """Calcula todas las métricas de un motor para una temporada y las
    guarda en HistoricalMetrics + HistoricalCalibration (una fila por
    bucket). Devuelve el mismo dict calculado, para uso inmediato en
    reportes sin tener que releer la base."""
    session_factory = session_factory or SessionLocal
    session = session_factory()
    try:
        predictions = (
            session.query(HistoricalPrediction)
            .filter_by(season_year=season_year, source=source)
            .all()
        )
        confidences, hits = _confidences_and_hits(predictions)
        home_probs, outcomes = _home_probs_and_outcomes(predictions)

        n = len(confidences)
        accuracy = (sum(hits) / n) if n else None
        brier = brier_score(home_probs, outcomes)
        ll = log_loss(home_probs, outcomes)
        buckets = calibration_buckets(confidences, hits)
        ece = expected_calibration_error(buckets, n)
        mce = maximum_calibration_error(buckets)
        sharp = sharpness(confidences)
        ci = bootstrap_ci(hits, lambda xs: sum(xs) / len(xs)) if n >= 2 else None

        metrics = {
            "n_sample": n, "accuracy": accuracy, "brier_score": brier, "log_loss": ll,
            "ece": ece, "mce": mce, "sharpness": sharp, "accuracy_ci": ci,
        }

        for name, value in metrics.items():
            if name == "accuracy_ci":
                continue
            session.add(HistoricalMetrics(
                run_id=run_id, season_year=season_year, source=source,
                metric_name=name, metric_value=value, n_sample=n,
            ))
        if ci is not None:
            session.add(HistoricalMetrics(
                run_id=run_id, season_year=season_year, source=source,
                metric_name="accuracy_ci_bootstrap", metric_value=None, n_sample=n,
                extra_json=json.dumps({"low": ci[0], "high": ci[1]}),
            ))

        for b in buckets:
            if b["n"] == 0:
                continue
            session.add(HistoricalCalibration(
                run_id=run_id, season_year=season_year, source=source,
                bucket_label=b["label"], bucket_low=b["low"], bucket_high=b["high"],
                n=b["n"], hits=b["hits"], avg_confidence=b["avg_confidence"],
                hit_rate=b["hit_rate"], gap=b["gap"],
            ))
        session.commit()
        return metrics
    finally:
        session.close()


def validate_all_sources(season_year: int, run_id: int, session_factory=None) -> dict:
    return {source: validate_source(season_year, source, run_id, session_factory) for source in SOURCES}


def compare_seasons_drift(source: str, seasons: list[int], session_factory=None) -> dict:
    """Compara accuracy/Brier de un motor a través de varias temporadas --
    NUNCA promedia las temporadas entre sí (ver regla de 'nunca mezclar
    automáticamente'), solo las pone una al lado de la otra para que el
    lector compare."""
    session_factory = session_factory or SessionLocal
    session = session_factory()
    try:
        by_season = {}
        for season_year in seasons:
            predictions = (
                session.query(HistoricalPrediction)
                .filter_by(season_year=season_year, source=source)
                .all()
            )
            confidences, hits = _confidences_and_hits(predictions)
            home_probs, outcomes = _home_probs_and_outcomes(predictions)
            n = len(confidences)
            by_season[season_year] = {
                "n": n,
                "accuracy": (sum(hits) / n) if n else None,
                "brier_score": brier_score(home_probs, outcomes),
            }
    finally:
        session.close()

    accuracies = [v["accuracy"] for v in by_season.values() if v["accuracy"] is not None]
    drift_detected = False
    max_spread = None
    if len(accuracies) >= 2:
        max_spread = max(accuracies) - min(accuracies)
        drift_detected = max_spread > 0.15  # umbral documentado, no un veredicto automático de "está roto"

    return {"source": source, "by_season": by_season, "max_accuracy_spread": max_spread, "drift_flagged": drift_detected}
