"""Pilar Game Context -- unico pilar que consume `ContextSignals` (ademas
del `GameSnapshot`), porque su proposito exacto es resumir en un advantage
discreto los hechos que ya detecto el Context Detector (Seccion 5). No
decide a favor de un equipo por default: los factores de contexto de esta
version son NEUTRALES entre home/away salvo la ventaja de local implicita
en tener menos viaje (aproximacion: el visitante es quien viaja)."""

from __future__ import annotations

from jsa.domain.models import ContextSignals, GameSnapshot, PillarAdvantage
from jsa.engine.pillars.base import PILLAR_CONTRACT_VERSIONS


def evaluate(snapshot: GameSnapshot, context: ContextSignals) -> PillarAdvantage:
    level = 0
    notes: list[str] = []

    if context.extreme_travel:
        # El campo travel_distance en GameSnapshot no distingue de quien es
        # el viaje -- se interpreta como carga del equipo visitante
        # (aproximacion estandar: el local no viaja para su propio juego).
        level -= 1
        notes.append("viaje extremo penaliza al visitante")

    if context.double_header:
        notes.append("doble cartelera detectada (neutral por lado, ya capturado por Rule Engine)")

    if context.extreme_weather:
        notes.append("clima extremo detectado (neutral por lado, aumenta incertidumbre, no advantage)")

    level = max(-2, min(2, level))
    explanation = "; ".join(notes) if notes else "sin senales de contexto relevantes"
    return PillarAdvantage(
        pillar="context",
        advantage=level,
        explanation=f"{explanation} -> advantage={level:+d}.",
        pillar_contract_version=PILLAR_CONTRACT_VERSIONS["context"],
    )
