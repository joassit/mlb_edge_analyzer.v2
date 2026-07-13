"""Los 7 evaluadores de pilar -- Seccion 7.1 del spec JSA v3.0."""

from __future__ import annotations

from jsa.domain.models import ContextSignals, GameSnapshot, PillarAdvantage
from jsa.engine.pillars import bullpen, context as context_pillar, historical, offense, starter, team_quality, trend


def evaluate_all_pillars(snapshot: GameSnapshot, context: ContextSignals) -> list[PillarAdvantage]:
    """Evalua los 7 pilares base, en el orden canonico del spec. Pura
    funcion de `snapshot`/`context` -- ningun pilar hace I/O."""
    return [
        starter.evaluate(snapshot),
        bullpen.evaluate(snapshot),
        offense.evaluate(snapshot),
        team_quality.evaluate(snapshot),
        context_pillar.evaluate(snapshot, context),
        trend.evaluate(snapshot),
        historical.evaluate(snapshot),
    ]
