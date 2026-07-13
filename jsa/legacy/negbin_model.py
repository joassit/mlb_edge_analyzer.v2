"""Probabilidad de victoria via Binomial Negativo (NB2) -- portado tal cual
de `mlb_edge_analyzer.v2/model/negbin_model.py`. Ver `jsa/legacy/README.md`:
rama secundaria de benchmarking, nunca el motor primario de JSA.

Captura sobredispersion de carreras (varianza = mu + mu^2/k) que
Skellam/Poisson subestima -- Poisson es el caso limite k -> infinito. La
diferencia de dos Binomiales Negativas independientes no tiene forma
cerrada, se calcula sumando la distribucion conjunta truncada a
`MAX_RUNS` carreras por equipo."""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import nbinom

MAX_RUNS = 30  # P(un equipo anota mas de 30 carreras) es numericamente cero.


def _nb_params(mu: float, k: float) -> tuple[float, float]:
    """Convierte (media mu, dispersion k) a (n, p) para scipy.stats.nbinom
    bajo la parametrizacion NB2 (varianza = mu + mu^2/k): p = k/(k+mu), n = k."""
    mu = max(mu, 1e-6)
    p = k / (k + mu)
    n = k
    return n, p


def _run_pmf(mu: float, k: float, max_runs: int = MAX_RUNS) -> np.ndarray:
    n, p = _nb_params(mu, k)
    runs = np.arange(max_runs + 1)
    return nbinom.pmf(runs, n, p)


def negbin_win_prob(mu_team: float, mu_opponent: float, k: float, max_runs: int = MAX_RUNS) -> float:
    """Probabilidad de que un equipo gane, dado su propio mu, el del
    rival, y el parametro de dispersion k (mismo k para ambos equipos,
    calibrado una sola vez contra resultados reales)."""
    pmf_team = _run_pmf(mu_team, k, max_runs)
    pmf_opp = _run_pmf(mu_opponent, k, max_runs)

    cdf_opp_strict_below = np.concatenate(([0.0], np.cumsum(pmf_opp)[:-1]))
    prob_win = float(np.sum(pmf_team * cdf_opp_strict_below))

    cdf_team_strict_below = np.concatenate(([0.0], np.cumsum(pmf_team)[:-1]))
    prob_loss = float(np.sum(pmf_opp * cdf_team_strict_below))

    denom = prob_win + prob_loss
    return (prob_win / denom) if denom > 0 else 0.5


def negbin_run_line_prob(
    mu_home: float, mu_away: float, k: float, line: float = 1.5, max_runs: int = MAX_RUNS, favorite_side: str = "home"
) -> tuple[float, float]:
    """Probabilidad de que el LOCAL/VISITANTE cubra su linea de hándicap,
    bajo NB2."""
    if favorite_side not in ("home", "away"):
        raise ValueError(f"favorite_side invalido: {favorite_side!r} (debe ser 'home' o 'away')")
    threshold = math.ceil(line)
    fav_mu, dog_mu = (mu_home, mu_away) if favorite_side == "home" else (mu_away, mu_home)

    pmf_fav = _run_pmf(fav_mu, k, max_runs)
    pmf_dog = _run_pmf(dog_mu, k, max_runs)
    cum_dog = np.cumsum(pmf_dog)

    fav_covers = 0.0
    for f in range(max_runs + 1):
        d_max = f - threshold
        if d_max >= 0:
            fav_covers += pmf_fav[f] * cum_dog[min(d_max, max_runs)]
    dog_covers = 1.0 - fav_covers

    home_covers, away_covers = (fav_covers, dog_covers) if favorite_side == "home" else (dog_covers, fav_covers)
    return float(home_covers), float(away_covers)


def negbin_totals_prob(mu_home: float, mu_away: float, k: float, line: float, max_runs: int = MAX_RUNS) -> tuple[float, float]:
    """Probabilidad de Over/Under bajo NB2 (convolucion de las dos PMF
    truncadas -- la suma de dos NB independientes no tiene forma cerrada)."""
    pmf_home = _run_pmf(mu_home, k, max_runs)
    pmf_away = _run_pmf(mu_away, k, max_runs)
    pmf_total = np.convolve(pmf_home, pmf_away)

    if line == int(line):
        line_int = int(line)
        push_prob = float(pmf_total[line_int]) if line_int < len(pmf_total) else 0.0
        remaining = 1.0 - push_prob
        threshold = min(line_int - 1, len(pmf_total) - 1)
        under_raw = float(np.sum(pmf_total[: threshold + 1])) if threshold >= 0 else 0.0
        under_prob = (under_raw / remaining) if remaining > 0 else 0.5
        over_prob = 1.0 - under_prob
        return over_prob, under_prob

    threshold = min(math.floor(line), len(pmf_total) - 1)
    under_prob = float(np.sum(pmf_total[: threshold + 1]))
    over_prob = 1.0 - under_prob
    return over_prob, under_prob
