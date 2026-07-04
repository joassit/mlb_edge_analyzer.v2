"""
Probabilidad de victoria vía distribución de Skellam (diferencia de dos
Poisson) — técnica estándar en sabermetría/modelos deportivos para convertir
carreras esperadas en probabilidad de ganar.

Un juego real de MLB no puede terminar en empate (hay entradas extra), pero
el modelo de Poisson/Skellam sí le asigna probabilidad a un empate en 9
entradas. La corrección estándar es renormalizar excluyendo esa probabilidad
de empate, repartiéndola proporcionalmente entre ambos equipos.
"""

from scipy.stats import skellam


def skellam_win_prob(mu_team: float, mu_opponent: float) -> float:
    """
    Probabilidad de que un equipo gane, dado su propio mu (carreras
    esperadas) y el del rival, usando Skellam + renormalización por empate.
    """
    mu_team = max(mu_team, 0.05)
    mu_opponent = max(mu_opponent, 0.05)

    prob_win = 1.0 - skellam.cdf(0, mu_team, mu_opponent)
    prob_loss = skellam.cdf(-1, mu_team, mu_opponent)

    denom = prob_win + prob_loss
    if denom <= 0:
        return 0.5
    return prob_win / denom
