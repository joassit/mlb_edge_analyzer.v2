"""
Probabilidad de victoria vía Binomial Negativo (NB2), EN PARALELO al modelo
de Skellam existente (model/skellam_model.py) -- no lo reemplaza, se guarda
aparte en cada predicción para comparar ambos en el tracking.

El Skellam asume carreras ~ Poisson (varianza = media). El tracking real
(ver reports/reporte_20260705.csv) muestra sobredispersión: el modelo
proyectó un promedio de 7.7 carreras totales vs. 10.4 reales ese día, con
juegos de 17-19 carreras que a Poisson casi no le caben. El Binomial
Negativo, bajo la parametrización NB2 (la misma que usan statsmodels/GLM
para conteos sobredispersos), captura esa cola gorda con un parámetro de
dispersión k: entre más chico k, más sobredispersión. Poisson es el caso
límite k -> infinito (ver test_negbin_converges_to_skellam_as_k_grows en
tests/test_negbin_model.py, que protege justo esa convergencia).

A diferencia de Skellam (la diferencia de dos Poisson es otra distribución
con forma cerrada), la diferencia de dos Binomiales Negativas independientes
NO tiene forma cerrada -- se calcula sumando la distribución conjunta
truncada a MAX_RUNS carreras por equipo, con el mismo criterio de
renormalización de empate que usa skellam_model.py: la probabilidad de
empate en 9 entradas (posible bajo el modelo, imposible en un juego real)
se descarta y el resto se renormaliza proporcionalmente entre ambos lados.
"""

import math

import numpy as np
from scipy.stats import nbinom

MAX_RUNS = 30  # truncamiento de la suma -- P(un equipo anota más de 30
               # carreras) es numéricamente cero para cualquier mu real de MLB.


def _nb_params(mu: float, k: float) -> tuple[float, float]:
    """
    Convierte (media mu, dispersión k) a los parámetros (n, p) que espera
    scipy.stats.nbinom, bajo la parametrización NB2: varianza = mu + mu^2/k.

    Resolver mean = n(1-p)/p = mu y var = n(1-p)/p^2 = mu + mu^2/k para
    (n, p) da, de forma exacta:
        p = k / (k + mu)
        n = k
    Esta es la conversión donde la mayoría de las implementaciones de NB2 se
    equivocan (confundir n con mu, o p con 1-p, o mezclar la parametrización
    NB1). test_negbin_model.py la protege por dos lados: comparando
    media/varianza contra el objetivo, y comparando contra Skellam en el
    límite k -> infinito (si la conversión estuviera mal, esa convergencia
    no se daría).
    """
    mu = max(mu, 1e-6)
    p = k / (k + mu)
    n = k
    return n, p


def _run_pmf(mu: float, k: float, max_runs: int = MAX_RUNS) -> np.ndarray:
    """PMF de carreras de un equipo, para 0..max_runs carreras, bajo NB2(mu, k)."""
    n, p = _nb_params(mu, k)
    runs = np.arange(max_runs + 1)
    return nbinom.pmf(runs, n, p)


def negbin_win_prob(mu_team: float, mu_opponent: float, k: float,
                     max_runs: int = MAX_RUNS) -> float:
    """
    Probabilidad de que un equipo gane, dado su propio mu (carreras
    esperadas), el del rival, y el parámetro de dispersión k -- el mismo k
    para ambos equipos, calibrado una sola vez contra resultados reales
    (ver scripts/calibrate_dispersion.py), no por equipo.

    Suma la distribución conjunta (asume independencia entre las carreras de
    ambos equipos, igual que Skellam) truncada a max_runs por equipo, y
    renormaliza excluyendo el empate -- mismo criterio que
    skellam_model.skellam_win_prob.
    """
    pmf_team = _run_pmf(mu_team, k, max_runs)
    pmf_opp = _run_pmf(mu_opponent, k, max_runs)

    # P(oponente <= r-1), es decir "estrictamente menos que r carreras",
    # con el caso r=0 en 0 (nadie anota menos que 0).
    cdf_opp_strict_below = np.concatenate(([0.0], np.cumsum(pmf_opp)[:-1]))
    prob_win = float(np.sum(pmf_team * cdf_opp_strict_below))

    cdf_team_strict_below = np.concatenate(([0.0], np.cumsum(pmf_team)[:-1]))
    prob_loss = float(np.sum(pmf_opp * cdf_team_strict_below))

    denom = prob_win + prob_loss
    if denom <= 0:
        return 0.5
    return prob_win / denom


def negbin_run_line_prob(mu_home: float, mu_away: float, k: float, line: float = 1.5,
                          max_runs: int = MAX_RUNS, favorite_side: str = "home") -> tuple[float, float]:
    """
    Probabilidad de que el LOCAL cubra su línea y de que el VISITANTE cubra
    la suya, bajo NB2 -- misma pregunta y misma semántica de favorite_side
    que model.markets.run_line_prob, sobre la distribución de colas gordas
    en vez de Poisson/Skellam. Con línea X.5 un empate exacto en la
    cobertura es imposible, así que ambas probabilidades ya suman 1.0 sin
    necesidad de renormalizar.
    """
    if favorite_side not in ("home", "away"):
        raise ValueError(f"favorite_side inválido: {favorite_side!r} (debe ser 'home' o 'away')")
    threshold = math.ceil(line)  # 1.5 -> 2 carreras de diferencia
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


def negbin_totals_prob(mu_home: float, mu_away: float, k: float, line: float,
                        max_runs: int = MAX_RUNS) -> tuple[float, float]:
    """
    Probabilidad de Over/Under bajo NB2. A diferencia de Poisson (la suma de
    dos Poisson es otra Poisson con tasa sumada, ver model.markets.totals_prob),
    la suma de dos Binomiales Negativas independientes no tiene forma cerrada
    simple -- se obtiene convolucionando las dos PMF truncadas.
    """
    pmf_home = _run_pmf(mu_home, k, max_runs)
    pmf_away = _run_pmf(mu_away, k, max_runs)
    pmf_total = np.convolve(pmf_home, pmf_away)  # índice 0..2*max_runs

    threshold = min(math.floor(line), len(pmf_total) - 1)
    under_prob = float(np.sum(pmf_total[: threshold + 1]))
    over_prob = 1.0 - under_prob
    return over_prob, under_prob
