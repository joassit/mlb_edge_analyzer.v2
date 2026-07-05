"""
Conversión entre cuotas americanas (moneyline), probabilidad implícita,
fair odds y el edge de tu modelo contra el mercado.
"""


def implied_prob(odds: float) -> float:
    """Convierte una cuota americana a probabilidad implícita (incluye el vig de la casa)."""
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def fair_odds(model_p: float) -> float:
    """
    Convierte la probabilidad de tu modelo a la cuota americana 'justa'
    (sin margen de casa) equivalente.
    """
    if model_p <= 0 or model_p >= 1:
        raise ValueError("model_p debe estar entre 0 y 1 (exclusivo)")
    if model_p >= 0.5:
        return -100 * model_p / (1 - model_p)
    return 100 * (1 - model_p) / model_p


def edge(model_p: float, imp_p: float) -> float:
    """
    Diferencia entre lo que dice tu modelo y lo que implica la cuota del mercado.
    Positivo = tu modelo ve más valor del que está pagando la casa.
    """
    return model_p - imp_p


def expected_value(model_p: float, odds: float) -> float:
    """
    Valor esperado por UNIDAD apostada, dado lo que cree tu modelo y la
    cuota real del mercado. Esto es lo que de verdad importa en apuestas:
    un modelo con menos accuracy pero mejor EV puede ganar más dinero que
    uno con más accuracy pero peor EV.

    EV > 0  -> apuesta con valor esperado positivo a largo plazo
    EV <= 0 -> no hay ventaja real, aunque el modelo "tenga razón" a veces
    """
    b = (100 / abs(odds)) if odds < 0 else (odds / 100)  # ganancia neta por unidad si gana
    return model_p * b - (1 - model_p)


def no_vig_probs(odds_a: float, odds_b: float) -> tuple[float, float]:
    """
    Normaliza las cuotas de los dos lados de un mismo mercado (moneyline)
    para remover el vig, devolviendo el consenso "justo" del mercado sin el
    margen de la casa.

    Esta es la base para medir Closing Line Value (CLV): comparar la
    probabilidad de tu modelo contra la probabilidad implícita de UN lado
    (con vig incluido) infla o entierra el edge según cuánto margen cobre
    la casa. Comparar contra el consenso sin vig de ambos lados es lo que
    permite distinguir skill real de varianza favorable en muestra chica.
    """
    p_a, p_b = implied_prob(odds_a), implied_prob(odds_b)
    total = p_a + p_b
    if total <= 0:
        return 0.5, 0.5
    return p_a / total, p_b / total


def kelly_fraction(model_p: float, odds: float, fraction: float = 0.25) -> float:
    """
    Tamaño de apuesta sugerido como fracción del bankroll, usando Kelly
    fraccionado (fraction=0.25 = 1/4 Kelly, más conservador que Kelly completo).
    Devuelve 0 si no hay edge positivo.
    """
    b = (100 / abs(odds)) if odds < 0 else (odds / 100)  # ganancia neta por unidad apostada
    q = 1 - model_p
    full_kelly = (b * model_p - q) / b
    return max(0.0, full_kelly * fraction)
