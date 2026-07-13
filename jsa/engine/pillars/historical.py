"""Pilar Historical Favorite Context -- STUB honesto: `GameSnapshot` no
trae historial head-to-head entre los dos equipos (requiere una fuente no
wireada en esta entrega, ver `jsa/docs/ROADMAP.md`). Devuelve siempre
advantage=0, calculado y reportado igual que los demas pilares, pero
transparente sobre su propia limitacion."""

from __future__ import annotations

from jsa.domain.models import GameSnapshot, PillarAdvantage
from jsa.engine.pillars.base import PILLAR_CONTRACT_VERSIONS


def evaluate(snapshot: GameSnapshot) -> PillarAdvantage:
    return PillarAdvantage(
        pillar="historical",
        advantage=0,
        explanation=(
            "Sin fuente de datos de historial head-to-head wireada en esta entrega -- "
            "advantage neutral por diseno, no un calculo real. Ver ROADMAP."
        ),
        pillar_contract_version=PILLAR_CONTRACT_VERSIONS["historical"],
    )
