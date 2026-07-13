"""Pilar Recent Trend -- STUB honesto: `GameSnapshot` no trae todavia
resultados de los ultimos N juegos de cada equipo (requiere una fuente de
game logs no wireada en esta entrega, ver `jsa/docs/ROADMAP.md`). Devuelve
siempre advantage=0 en vez de inventar una senal -- se calcula y se reporta
igual (Seccion 7.1: todo pilar activo debe evaluarse), pero de forma
transparente sobre su propia limitacion."""

from __future__ import annotations

from jsa.domain.models import GameSnapshot, PillarAdvantage
from jsa.engine.pillars.base import PILLAR_CONTRACT_VERSIONS


def evaluate(snapshot: GameSnapshot) -> PillarAdvantage:
    return PillarAdvantage(
        pillar="trend",
        advantage=0,
        explanation=(
            "Sin fuente de datos de tendencia reciente (ultimos N juegos) wireada en esta "
            "entrega -- advantage neutral por diseno, no un calculo real. Ver ROADMAP."
        ),
        pillar_contract_version=PILLAR_CONTRACT_VERSIONS["trend"],
    )
