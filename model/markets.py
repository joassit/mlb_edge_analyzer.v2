"""
Mercados adicionales a Moneyline, construidos sobre la MISMA proyección de
carreras (mu_home, mu_away) que ya usa el modelo Skellam — no es una fuente
de datos nueva, es la misma matemática aplicada a preguntas distintas.

Run Line: ¿el favorito gana por 2+ carreras (cubre -1.5)?
Totales: ¿el total de carreras del juego es más o menos que una línea?

A diferencia de Moneyline, estos dos mercados NO necesitan renormalización
por empate: con una línea de X.5, un empate exacto es matemáticamente
imposible (no hay medias carreras), así que las probabilidades complementarias
ya suman 1.0 de forma exacta.
"""

import math
from scipy.stats import skellam, poisson


def run_line_prob(mu_home: float, mu_away: float, line: float = 1.5) -> tuple[float, float]:
    """
    Probabilidad de que el LOCAL cubra -line (gane por más de `line` carreras)
    y de que el VISITANTE cubra +line (pierda por menos de `line`, o gane).

    line=1.5 es el estándar de MLB (el favorito debe ganar por 2+ para cubrir).
    """
    threshold = math.ceil(line)  # 1.5 -> 2 carreras de diferencia
    home_covers = 1.0 - skellam.cdf(threshold - 1, mu_home, mu_away)
    away_covers = 1.0 - home_covers
    return home_covers, away_covers


def totals_prob(mu_home: float, mu_away: float, line: float) -> tuple[float, float]:
    """
    Probabilidad de Over/Under sobre el total de carreras del juego.
    La suma de carreras de dos Poisson independientes es otra Poisson,
    con tasa = mu_home + mu_away.
    """
    mu_total = mu_home + mu_away
    threshold = math.floor(line)  # ej. línea 8.5 -> 8
    under_prob = poisson.cdf(threshold, mu_total)
    over_prob = 1.0 - under_prob
    return over_prob, under_prob


def fair_total_line(mu_home: float, mu_away: float) -> float:
    """Total de carreras 'justo' según el modelo (no una línea real de casa de apuestas)."""
    return mu_home + mu_away