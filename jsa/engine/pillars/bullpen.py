"""Pilar Bullpen -- compara el ERA de bullpen de ambos equipos, con un
ajuste por disponibilidad de closer cuando el dato existe."""

from __future__ import annotations

from jsa.domain.models import GameSnapshot, PillarAdvantage
from jsa.engine.pillars.base import PILLAR_CONTRACT_VERSIONS, discretize_diff, no_data_advantage

_UNIT_ERA_RUNS = 0.45
_CLOSER_UNAVAILABLE_PENALTY = 0.30  # runs-equivalentes restados al equipo sin closer disponible


def evaluate(snapshot: GameSnapshot) -> PillarAdvantage:
    if snapshot.home_bullpen_era is None and snapshot.away_bullpen_era is None:
        return no_data_advantage("bullpen", "ningun equipo tiene ERA de bullpen disponible")

    home_era = snapshot.home_bullpen_era if snapshot.home_bullpen_era is not None else snapshot.away_bullpen_era
    away_era = snapshot.away_bullpen_era if snapshot.away_bullpen_era is not None else snapshot.home_bullpen_era

    home_effective = home_era + (_CLOSER_UNAVAILABLE_PENALTY if snapshot.home_closer_available is False else 0.0)
    away_effective = away_era + (_CLOSER_UNAVAILABLE_PENALTY if snapshot.away_closer_available is False else 0.0)

    diff = away_effective - home_effective
    advantage = discretize_diff(diff, _UNIT_ERA_RUNS)

    explanation = (
        f"ERA de bullpen: home={home_era:.2f} (efectivo={home_effective:.2f}), "
        f"away={away_era:.2f} (efectivo={away_effective:.2f}). "
        f"Diferencia={diff:+.2f} -> advantage={advantage:+d}."
    )
    return PillarAdvantage(
        pillar="bullpen", advantage=advantage, explanation=explanation,
        pillar_contract_version=PILLAR_CONTRACT_VERSIONS["bullpen"],
    )
