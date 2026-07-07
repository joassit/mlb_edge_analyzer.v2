"""
Ajustes estadísticos sobre los insumos crudos, antes de que entren al
modelo de probabilidad/proyección de carreras.

Estos valores (k_ip=60.0, exponent=1.8) son puntos de partida razonables
tomados de la literatura sabermétrica estándar (shrinkage hacia el
promedio de liga para ERA de muestra chica; sensibilidad no lineal de
carreras a diferencial ofensivo), NO constantes validadas contra un
backtest de este proyecto todavía. Son una hipótesis mejor que el ERA
crudo sin ajustar, no una calibración — reemplázalos en cuanto haya
suficiente historial en `feature_snapshots` para ajustar estos parámetros
con evidencia real en vez de con juicio experto.
"""


def shrunk_era(era: float, innings: float, league_era: float = 4.30, k_ip: float = 60.0) -> float:
    """
    Encoge el ERA observado hacia el promedio de liga en proporción
    inversa a las entradas lanzadas -- un abridor con 15 entradas en la
    temporada tiene un ERA con muchísimo ruido de muestra chica; uno con
    150 entradas, mucho menos. `k_ip` es el "peso en entradas" que se le da
    al prior de liga (a más k_ip, más shrinkage incluso con muestras
    grandes).
    """
    if innings <= 0:
        return league_era
    return (era * innings + league_era * k_ip) / (innings + k_ip)


def offense_factor(team_ops: float, league_ops: float, exponent: float = 1.8) -> float:
    """
    Factor ofensivo no lineal: un OPS 10% por encima de la liga no se
    traduce en 10% más carreras -- el efecto compone. Elevar el ratio a un
    exponente >1 refleja eso (a exponente=1.0 se recupera el ratio lineal
    que usaba el modelo antes de esta corrección).
    """
    return (team_ops / league_ops) ** exponent


# Re-export de conveniencia -- devig_two_way vive en model/edge.py como
# alias de no_vig_probs() (misma operación, no una función nueva). Se
# re-exporta aquí para quien importe ajustes estadísticos desde este
# módulo, sin duplicar la lógica de remover vig en dos lugares.
from model.edge import devig_two_way  # noqa: E402,F401
