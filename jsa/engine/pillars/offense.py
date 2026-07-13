"""Pilar Offensive Matchup -- compara el factor ofensivo (OPS relativo a
liga, no lineal) de ambos equipos."""

from __future__ import annotations

from jsa.config import MIN_PA_FOR_LEAGUE_OPS
from jsa.domain.models import GameSnapshot, PillarAdvantage
from jsa.engine.pillars.base import PILLAR_CONTRACT_VERSIONS, discretize_diff, no_data_advantage, offense_factor

_UNIT_FACTOR = 0.06
_LEAGUE_OPS_FALLBACK = 0.750


def evaluate(snapshot: GameSnapshot) -> PillarAdvantage:
    if snapshot.home_ops is None and snapshot.away_ops is None:
        return no_data_advantage("offense", "ningun equipo tiene OPS disponible")

    league_ops = snapshot.league_avg_ops or _LEAGUE_OPS_FALLBACK
    home_ops = snapshot.home_ops if snapshot.home_ops is not None else league_ops
    away_ops = snapshot.away_ops if snapshot.away_ops is not None else league_ops

    home_factor = offense_factor(home_ops, league_ops)
    away_factor = offense_factor(away_ops, league_ops)
    diff = home_factor - away_factor
    advantage = discretize_diff(diff, _UNIT_FACTOR)

    small_sample_note = ""
    if (snapshot.home_ops_pa_sample or 0) < MIN_PA_FOR_LEAGUE_OPS or (snapshot.away_ops_pa_sample or 0) < MIN_PA_FOR_LEAGUE_OPS:
        small_sample_note = " (nota: muestra chica en al menos un equipo, sin shrinkage adicional en esta entrega)"

    explanation = (
        f"OPS: home={home_ops:.3f} (factor={home_factor:.3f}), away={away_ops:.3f} "
        f"(factor={away_factor:.3f}), liga={league_ops:.3f}. Diferencia de factor={diff:+.3f} "
        f"-> advantage={advantage:+d}.{small_sample_note}"
    )
    return PillarAdvantage(
        pillar="offense", advantage=advantage, explanation=explanation,
        pillar_contract_version=PILLAR_CONTRACT_VERSIONS["offense"],
    )
