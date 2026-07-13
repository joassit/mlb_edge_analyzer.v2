"""Monte Carlo Audit -- Seccion 13.2 y 13.7bis del spec JSA v3.0.

**Nunca se usa para predecir juegos** (Principio de la Seccion 13.2) --
se usa para medir estabilidad: N simulaciones perturbando `PillarWeights`
alrededor de los pesos base, midiendo cuanto cambia el desempeño
(Brier-proxy) y que tan fragil es cada pilar. Opera sobre los
`PillarAdvantage` YA CALCULADOS y persistidos por `pipeline.py` -- nunca
vuelve a golpear la API ni re-evalua reglas, solo perturba los PESOS
(exactamente lo que pide la Seccion 13.2: "N simulaciones variando pesos
-> sensibilidad -> importancia real de cada pilar").

Requiere que `pipeline.run_season_ingestion()` ya haya corrido para la
temporada -- sin datos historicos reales, esto no tiene nada que auditar."""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field

from jsa.config import BASE_PILLAR_WEIGHTS
from jsa.domain.models import SEVEN_PILLARS
from jsa.historical import db as historical_db

# Umbral fuera del cual una probabilidad-proxy se considera "colapsada"
# hacia un extremo no realista bajo perturbacion (Probability Collapse,
# Seccion 13.7bis) -- ver nota sobre `_pseudo_probability` mas abajo.
_COLLAPSE_THRESHOLD = 0.97


def _pseudo_probability(evidence_score: float) -> float:
    """Transformacion logistica del Evidence Score (-2..+2) a una
    pseudo-probabilidad -- SOLO para medir Probability Collapse dentro de
    este audit. NUNCA se expone como `calibrated_probability` en ningun
    reporte: JSA no tiene calibracion real todavia (Seccion 8.4.1), y esta
    funcion no pretende serlo -- es una vara de medir interna, documentada
    como proxy, no un output del sistema."""
    return 1.0 / (1.0 + math.exp(-evidence_score))


def _sample_perturbed_weights(base_weights: dict[str, float], noise_scale: float, rng: random.Random) -> dict[str, float]:
    raw = {p: max(0.0, base_weights[p] + rng.gauss(0, noise_scale)) for p in base_weights}
    total = sum(raw.values())
    if total <= 0:
        return dict(base_weights)
    return {p: v / total for p, v in raw.items()}


@dataclass
class MonteCarloAuditResult:
    season: int
    n_simulations: int
    n_games: int
    noise_scale: float
    random_seed: int
    critical_failure_factor: str | None
    critical_failure_factor_correlation: float | None
    feature_stability: dict[str, float] = field(default_factory=dict)
    weight_stability: dict[str, float] = field(default_factory=dict)
    probability_collapse_rate: float = 0.0


def run_monte_carlo_audit(
    season: int, historical_database_url: str, n_simulations: int = 200, noise_scale: float = 0.05, random_seed: int = 42,
) -> MonteCarloAuditResult:
    engine = historical_db.get_engine(historical_database_url)
    historical_db.init_historical_storage(engine)  # tolera una base historica nueva/vacia
    reports = historical_db.reports_for_season(engine, season)

    games: list[tuple[dict[str, int], int]] = []  # (advantage por pilar, actual_home_win)
    games_by_pk = {g["game_pk"]: g for g in historical_db.games_for_season(engine, season)}
    for report_row in reports:
        game = games_by_pk.get(report_row["game_pk"])
        if game is None or game.get("winner") is None:
            continue
        payload = report_row["payload"]
        advantages = {p["pillar"]: p["advantage"] for p in payload["pillar_advantages"]}
        if not all(p in advantages for p in SEVEN_PILLARS):
            continue
        games.append((advantages, 1 if game["winner"] == "home" else 0))

    if not games:
        return MonteCarloAuditResult(
            season=season, n_simulations=0, n_games=0, noise_scale=noise_scale, random_seed=random_seed,
            critical_failure_factor=None, critical_failure_factor_correlation=None,
        )

    rng = random.Random(random_seed)

    weight_samples: dict[str, list[float]] = {p: [] for p in SEVEN_PILLARS}
    contribution_samples: dict[str, list[float]] = {p: [] for p in SEVEN_PILLARS}
    brier_per_sim: list[float] = []
    collapsed = 0
    total_predictions = 0

    for _ in range(n_simulations):
        weights = _sample_perturbed_weights(BASE_PILLAR_WEIGHTS, noise_scale, rng)
        for p in SEVEN_PILLARS:
            weight_samples[p].append(weights[p])

        sim_errors: list[float] = []
        pillar_avg_advantage: dict[str, float] = {p: 0.0 for p in SEVEN_PILLARS}
        for advantages, actual in games:
            evidence_score = sum(weights[p] * advantages[p] for p in SEVEN_PILLARS)
            prob_home = _pseudo_probability(evidence_score)
            sim_errors.append((prob_home - actual) ** 2)
            total_predictions += 1
            if prob_home >= _COLLAPSE_THRESHOLD or prob_home <= (1 - _COLLAPSE_THRESHOLD):
                collapsed += 1
            for p in SEVEN_PILLARS:
                pillar_avg_advantage[p] += advantages[p]

        for p in SEVEN_PILLARS:
            contribution_samples[p].append(weights[p] * (pillar_avg_advantage[p] / len(games)))

        brier_per_sim.append(sum(sim_errors) / len(sim_errors))

    weight_stability = {p: statistics.pvariance(weight_samples[p]) for p in SEVEN_PILLARS}
    feature_stability = {p: statistics.pvariance(contribution_samples[p]) for p in SEVEN_PILLARS}

    critical_pillar, critical_corr = None, None
    if n_simulations >= 3 and statistics.pvariance(brier_per_sim) > 0:
        best_abs_corr = -1.0
        for p in SEVEN_PILLARS:
            if statistics.pvariance(weight_samples[p]) == 0:
                continue
            try:
                corr = statistics.correlation(weight_samples[p], brier_per_sim)
            except statistics.StatisticsError:
                continue
            if abs(corr) > best_abs_corr:
                best_abs_corr = abs(corr)
                critical_pillar, critical_corr = p, corr

    return MonteCarloAuditResult(
        season=season, n_simulations=n_simulations, n_games=len(games), noise_scale=noise_scale, random_seed=random_seed,
        critical_failure_factor=critical_pillar, critical_failure_factor_correlation=critical_corr,
        feature_stability=feature_stability, weight_stability=weight_stability,
        probability_collapse_rate=(collapsed / total_predictions) if total_predictions else 0.0,
    )
