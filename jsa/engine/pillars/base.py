"""Utilidades compartidas por los 7 evaluadores de pilar -- Seccion 7.1/7.4.

Cada pilar es una funcion PURA de `GameSnapshot` (mas, para `context.py`,
del `ContextSignals` ya derivado de ese mismo snapshot): nunca golpea una
API ni lee un reloj de pared, para que sea recalculable byte a byte desde
un snapshot ya congelado (Principio 3, Principio 6).

`pillar_contract_version` (Seccion 7.4): cada pilar declara su propia
version semver, independiente de `model_version` -- si la formula interna
de un pilar cambia, su version debe subir aqui. Ningun otro modulo debe
declarar estas versiones por su cuenta (fuente unica de verdad, consumida
por `governance/manifest.py` para poblar `RunManifest.pillar_versions`)."""

from __future__ import annotations

from jsa.config import OFFENSE_FACTOR_EXPONENT, SHRINKAGE_K_IP
from jsa.domain.models import PillarAdvantage

PILLAR_CONTRACT_VERSIONS: dict[str, str] = {
    "starter": "starter@1.0.0",
    "bullpen": "bullpen@1.0.0",
    "offense": "offense@1.0.0",
    "team_quality": "team_quality@1.0.0",
    "context": "context@1.0.0",
    "trend": "trend@1.0.0-stub",
    "historical": "historical@1.0.0-stub",
}


def shrunk_era(era: float, innings: float, league_era: float, k_ip: float = SHRINKAGE_K_IP) -> float:
    """Encoge el ERA observado hacia el promedio de liga en proporcion
    inversa a las entradas lanzadas -- mismo shrinkage bayesiano simple que
    `mlb_edge_analyzer.v2/model/adjustments.py::shrunk_era`, puntos de
    partida de literatura sabermetrica estandar, no calibrados todavia
    contra historial propio de JSA."""
    if innings is None or innings <= 0:
        return league_era
    return (era * innings + league_era * k_ip) / (innings + k_ip)


def offense_factor(team_ops: float, league_ops: float, exponent: float = OFFENSE_FACTOR_EXPONENT) -> float:
    """Factor ofensivo no lineal -- mismo criterio que
    `mlb_edge_analyzer.v2/model/adjustments.py::offense_factor`."""
    if league_ops <= 0:
        return 1.0
    return (team_ops / league_ops) ** exponent


def discretize_diff(diff: float, unit: float) -> int:
    """Convierte una diferencia continua (home - away, positivo = ventaja
    home) a un nivel discreto en {-2,-1,0,1,2}, redondeando al entero mas
    cercano en unidades de `unit` y recortando al rango permitido por el
    contrato (Seccion 7.1)."""
    if unit <= 0:
        return 0
    level = round(diff / unit)
    return max(-2, min(2, level))


def no_data_advantage(pillar: str, reason: str) -> PillarAdvantage:
    return PillarAdvantage(
        pillar=pillar,
        advantage=0,
        explanation=f"Sin datos suficientes para evaluar {pillar}: {reason}.",
        pillar_contract_version=PILLAR_CONTRACT_VERSIONS[pillar],
    )
