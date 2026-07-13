"""Pilar Team Quality -- proxy de completitud de roster (lesiones clave +
disponibilidad de closer), deliberadamente distinto del pilar Offense (que
mide produccion ofensiva, no salud de plantilla) y del pilar Bullpen (que
mide desempeno, no disponibilidad de cierre).

Limitacion honesta: el spec no fija los insumos exactos de este pilar y
`GameSnapshot` no trae record de temporada ni diferencial de carreras
(ver ROADMAP para una futura migracion aditiva que los incorpore) -- esta
version usa las unicas senales de "calidad de plantilla" que si existen
hoy en el snapshot."""

from __future__ import annotations

from jsa.domain.models import GameSnapshot, PillarAdvantage
from jsa.engine.pillars.base import PILLAR_CONTRACT_VERSIONS

_PER_INJURY_LEVEL = 1  # cada lesion clave de diferencia mueve 1 nivel
_CLOSER_UNAVAILABLE_LEVEL = 1


def evaluate(snapshot: GameSnapshot) -> PillarAdvantage:
    injury_diff = len(snapshot.away_key_injuries) - len(snapshot.home_key_injuries)
    level = max(-2, min(2, injury_diff * _PER_INJURY_LEVEL))

    closer_note = ""
    if snapshot.home_closer_available is False and snapshot.away_closer_available is not False:
        level = max(-2, level - _CLOSER_UNAVAILABLE_LEVEL)
        closer_note = " Closer de home no disponible."
    elif snapshot.away_closer_available is False and snapshot.home_closer_available is not False:
        level = min(2, level + _CLOSER_UNAVAILABLE_LEVEL)
        closer_note = " Closer de away no disponible."

    explanation = (
        f"Lesiones clave: home={len(snapshot.home_key_injuries)}, away={len(snapshot.away_key_injuries)}."
        f"{closer_note} -> advantage={level:+d}."
    )
    return PillarAdvantage(
        pillar="team_quality", advantage=level, explanation=explanation,
        pillar_contract_version=PILLAR_CONTRACT_VERSIONS["team_quality"],
    )
