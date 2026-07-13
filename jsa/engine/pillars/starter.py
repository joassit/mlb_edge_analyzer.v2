"""Pilar Starter -- compara el ERA (proxy de xERA, ver ROADMAP) de ambos
abridores, con shrinkage hacia el promedio de liga por muestra chica."""

from __future__ import annotations

from jsa.config import LEAGUE_AVG_ERA
from jsa.domain.models import GameSnapshot, PillarAdvantage
from jsa.engine.pillars.base import PILLAR_CONTRACT_VERSIONS, discretize_diff, no_data_advantage, shrunk_era

_UNIT_ERA_RUNS = 0.55  # heuristico: ~0.55 de ERA encogido = un nivel de advantage


def evaluate(snapshot: GameSnapshot) -> PillarAdvantage:
    if snapshot.home_starter_xera is None and snapshot.away_starter_xera is None:
        return no_data_advantage("starter", "ningun abridor tiene ERA/xERA disponible")

    league_era = snapshot.league_avg_era or LEAGUE_AVG_ERA
    home_era = (
        shrunk_era(snapshot.home_starter_xera, snapshot.home_starter_ip_sample or 0, league_era)
        if snapshot.home_starter_xera is not None
        else league_era
    )
    away_era = (
        shrunk_era(snapshot.away_starter_xera, snapshot.away_starter_ip_sample or 0, league_era)
        if snapshot.away_starter_xera is not None
        else league_era
    )

    diff = away_era - home_era  # ERA mas bajo es mejor -> positivo favorece a home
    advantage = discretize_diff(diff, _UNIT_ERA_RUNS)

    explanation = (
        f"ERA encogido hacia liga: home={home_era:.2f}, away={away_era:.2f} "
        f"(liga={league_era:.2f}). Diferencia={diff:+.2f} -> advantage={advantage:+d}."
    )
    return PillarAdvantage(
        pillar="starter", advantage=advantage, explanation=explanation,
        pillar_contract_version=PILLAR_CONTRACT_VERSIONS["starter"],
    )
