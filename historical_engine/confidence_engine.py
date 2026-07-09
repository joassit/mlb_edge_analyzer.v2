"""
Historical Confidence Engine -- el "cuarto motor", junto a Skellam,
Binomial Negativo y el Heurístico.

Regla no negociable (ver tests/test_historical_confidence_engine.py):
ESTE MOTOR NUNCA GENERA NI DEVUELVE UNA PROBABILIDAD DE GANAR. Recibe las
probabilidades ya calculadas por los otros tres motores y las usa
ÚNICAMENTE como llave de búsqueda dentro del histórico ya acumulado en
HistoricalCalibration -- nunca las transforma, promedia, ni corrige. Lo
que devuelve es una capa de EVIDENCIA interpretable (qué tan seguido,
históricamente, un bucket de confianza como este acertó), no una
predicción nueva. `HistoricalConfidenceReport` no tiene ningún campo que
un integrador pueda confundir con "la probabilidad correcta a usar" --
a propósito, para que sea estructuralmente imposible conectarlo en el
lugar de Skellam/NegBin sin que sea obvio que se está haciendo otra cosa.
"""

from dataclasses import dataclass, field

from historical_engine.db import HistoricalCalibration, SessionLocal

# Mismos 6 buckets que tracking.results_tracker._CALIBRATION_BUCKETS de
# producción -- NO se importa esa lista (evitar acoplar este motor a un
# detalle interno de producción), se redefine igual acá a propósito.
_CONFIDENCE_BUCKETS = [
    (0.50, 0.55, "50-55%"),
    (0.55, 0.60, "55-60%"),
    (0.60, 0.65, "60-65%"),
    (0.65, 0.70, "65-70%"),
    (0.70, 0.75, "70-75%"),
    (0.75, 1.01, "75%+"),
]

_MIN_N_FOR_MODERATE_EVIDENCE = 30
_MIN_N_FOR_HIGH_EVIDENCE = 200  # mismo umbral filosófico que producción (200 picks)


def _bucket_for(confidence: float) -> str:
    for low, high, label in _CONFIDENCE_BUCKETS:
        if low <= confidence < high:
            return label
    return "75%+"


@dataclass
class HistoricalConfidenceReport:
    """Salida interpretable del Historical Confidence Engine. Nada acá es
    una probabilidad de resultado -- ver docstring del módulo."""
    bucket_label: str
    comparable_sample_n: int
    evidence_level: str            # "insuficiente" | "moderada" | "alta"
    historical_hit_rate: float | None       # tasa de acierto histórica en este bucket (informativo)
    historical_avg_confidence: float | None
    expected_calibration_gap: float | None  # hit_rate - avg_confidence histórico (informativo, no se aplica)
    robustness_level: str           # "baja" | "media" | "alta" -- consistencia entre temporadas
    seasons_represented: list = field(default_factory=list)
    detected_biases: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


class HistoricalConfidenceEngine:
    """
    Uso: HistoricalConfidenceEngine().evaluate(skellam_prob=..., negbin_prob=...,
    heuristic_prob=..., source_for_bucket="skellam", season=2026)

    `source_for_bucket` decide de qué motor se toma la confianza para
    buscar el bucket histórico comparable (default "skellam", la fuente
    activa de picks en producción, ver config.PICK_PROBABILITY_SOURCE) --
    nunca decide un resultado, solo qué columna de HistoricalCalibration
    consultar.
    """

    def __init__(self, session_factory=None):
        self._session_factory = session_factory or SessionLocal

    def evaluate(self, skellam_prob: float, negbin_prob: float, heuristic_prob: float,
                 source_for_bucket: str = "skellam", exclude_season: int | None = None) -> HistoricalConfidenceReport:
        probs = {"skellam": skellam_prob, "negbin": negbin_prob, "heuristic": heuristic_prob}
        confidence = max(probs[source_for_bucket], 1 - probs[source_for_bucket])
        bucket_label = _bucket_for(confidence)

        session = self._session_factory()
        try:
            query = (
                session.query(HistoricalCalibration)
                .filter(HistoricalCalibration.source == source_for_bucket)
                .filter(HistoricalCalibration.bucket_label == bucket_label)
            )
            if exclude_season is not None:
                # La temporada actual nunca se usa como su propia evidencia
                # histórica -- ver jerarquía producción > temporada actual >
                # histórico en historical_engine/__init__.py.
                query = query.filter(HistoricalCalibration.season_year != exclude_season)
            rows = query.all()
        finally:
            session.close()

        warnings, biases = [], []

        if not rows:
            return HistoricalConfidenceReport(
                bucket_label=bucket_label, comparable_sample_n=0, evidence_level="insuficiente",
                historical_hit_rate=None, historical_avg_confidence=None, expected_calibration_gap=None,
                robustness_level="baja", seasons_represented=[],
                warnings=["Sin historial de calibración para este bucket -- no hay evidencia comparable todavía."],
            )

        total_n = sum(r.n for r in rows)
        total_hits = sum(r.hits for r in rows)
        hit_rate = (total_hits / total_n) if total_n > 0 else None
        avg_confidence = (
            sum(r.avg_confidence * r.n for r in rows if r.avg_confidence is not None) / total_n
            if total_n > 0 else None
        )
        gap = (hit_rate - avg_confidence) if (hit_rate is not None and avg_confidence is not None) else None

        if total_n < _MIN_N_FOR_MODERATE_EVIDENCE:
            evidence_level = "insuficiente"
        elif total_n < _MIN_N_FOR_HIGH_EVIDENCE:
            evidence_level = "moderada"
        else:
            evidence_level = "alta"

        seasons = sorted({r.season_year for r in rows})
        per_season_hit_rate = []
        for s in seasons:
            season_rows = [r for r in rows if r.season_year == s]
            n_s = sum(r.n for r in season_rows)
            hits_s = sum(r.hits for r in season_rows)
            if n_s > 0:
                per_season_hit_rate.append(hits_s / n_s)

        if len(per_season_hit_rate) >= 2:
            spread = max(per_season_hit_rate) - min(per_season_hit_rate)
            if spread <= 0.10:
                robustness_level = "alta"
            elif spread <= 0.25:
                robustness_level = "media"
            else:
                robustness_level = "baja"
                biases.append(
                    f"Alta variación de acierto entre temporadas en el bucket {bucket_label} "
                    f"({min(per_season_hit_rate):.1%} a {max(per_season_hit_rate):.1%})."
                )
        else:
            robustness_level = "baja"
            warnings.append("Menos de 2 temporadas con datos para este bucket -- robustez no evaluable con confianza.")

        if gap is not None and gap < -0.10:
            biases.append(f"Históricamente sobreconfiado en este bucket (gap {gap:+.1%}).")
        elif gap is not None and gap > 0.10:
            biases.append(f"Históricamente subconfiado en este bucket (gap {gap:+.1%}).")

        if evidence_level == "insuficiente":
            warnings.append(
                f"n={total_n} en este bucket está por debajo del mínimo de evidencia moderada "
                f"({_MIN_N_FOR_MODERATE_EVIDENCE}) -- tratar como referencia, no como validación."
            )

        source_agreement = (skellam_prob > 0.5) == (negbin_prob > 0.5) == (heuristic_prob > 0.5)
        if not source_agreement:
            warnings.append("Los 3 motores no coinciden en el favorito para este juego -- señal de baja robustez del consenso.")

        return HistoricalConfidenceReport(
            bucket_label=bucket_label, comparable_sample_n=total_n, evidence_level=evidence_level,
            historical_hit_rate=hit_rate, historical_avg_confidence=avg_confidence,
            expected_calibration_gap=gap, robustness_level=robustness_level,
            seasons_represented=seasons, detected_biases=biases, warnings=warnings,
        )
