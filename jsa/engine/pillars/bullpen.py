"""Pilar Bullpen -- compara el ERA de bullpen de ambos equipos, con
shrinkage bayesiano hacia el promedio de liga por muestra chica (@1.1.0:
la MISMA `shrunk_era()` que ya usa `starter` -- antes de esto, bullpen
era el UNICO de los 7 pilares que comparaba un ERA crudo sin encoger,
ver ROADMAP) y un ajuste por disponibilidad de closer cuando el dato
existe.

Nota de comportamiento (@1.1.0): al adoptar el mismo patron que
`starter.py`, el fallback cuando un equipo no tiene ERA de bullpen
disponible tambien cambia -- antes usaba el ERA del RIVAL como proxy
(coincidencia con `discretize_diff`==0, "asumir paridad"); ahora usa el
promedio de liga, igual que `starter`, un criterio mas consistente entre
pilares."""

from __future__ import annotations

from jsa.config import LEAGUE_AVG_ERA
from jsa.domain.models import GameSnapshot, PillarAdvantage
from jsa.engine.pillars.base import PILLAR_CONTRACT_VERSIONS, discretize_diff, no_data_advantage, shrunk_era

_UNIT_ERA_RUNS = 0.45
_CLOSER_UNAVAILABLE_PENALTY = 0.30  # runs-equivalentes restados al equipo sin closer disponible


def evaluate(snapshot: GameSnapshot) -> PillarAdvantage:
    if snapshot.home_bullpen_era is None and snapshot.away_bullpen_era is None:
        return no_data_advantage("bullpen", "ningun equipo tiene ERA de bullpen disponible")

    league_era = snapshot.league_avg_era or LEAGUE_AVG_ERA
    home_era = (
        shrunk_era(snapshot.home_bullpen_era, snapshot.home_bullpen_ip_sample or 0, league_era)
        if snapshot.home_bullpen_era is not None
        else league_era
    )
    away_era = (
        shrunk_era(snapshot.away_bullpen_era, snapshot.away_bullpen_ip_sample or 0, league_era)
        if snapshot.away_bullpen_era is not None
        else league_era
    )

    home_effective = home_era + (_CLOSER_UNAVAILABLE_PENALTY if snapshot.home_closer_available is False else 0.0)
    away_effective = away_era + (_CLOSER_UNAVAILABLE_PENALTY if snapshot.away_closer_available is False else 0.0)

    diff = away_effective - home_effective
    advantage = discretize_diff(diff, _UNIT_ERA_RUNS)

    explanation = (
        f"ERA de bullpen encogido hacia liga: home={home_era:.2f} (efectivo={home_effective:.2f}), "
        f"away={away_era:.2f} (efectivo={away_effective:.2f}) (liga={league_era:.2f}). "
        f"Diferencia={diff:+.2f} -> advantage={advantage:+d}."
    )
    return PillarAdvantage(
        pillar="bullpen", advantage=advantage, explanation=explanation,
        pillar_contract_version=PILLAR_CONTRACT_VERSIONS["bullpen"],
    )
