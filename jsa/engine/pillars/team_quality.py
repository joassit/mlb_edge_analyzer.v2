"""Pilar Team Quality -- proxy de completitud de roster (lesiones clave +
disponibilidad de closer) y, desde @1.1.0, una senal defensiva de bajo
esfuerzo (fielding percentage de equipo) -- deliberadamente distinto del
pilar Offense (que mide produccion ofensiva, no salud/calidad de
plantilla) y del pilar Bullpen (que mide desempeno, no disponibilidad de
cierre).

`home/away_fielding_pct` fue validado via spike real contra
`teams/{id}/stats?stats=byDateRange&group=fielding` antes de agregarlo
(ver `jsa/docs/ROADMAP.md`) -- OAA/DRS descartados por no estar expuestos
por `statsapi.mlb.com`. Se usa fielding% en vez de errors/doublePlays
porque ya viene normalizado por MLB (errores sobre chances totales),
comparable directo entre equipos sin ajustar por volumen de juegos.

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
# Heuristico de partida (no calibrado contra historial propio de JSA
# todavia): en la muestra del spike, el fielding% de un mismo equipo
# vario entre .985 y .990 a lo largo de 5 temporadas -- 0.006 de
# diferencia entre home/away mueve como maximo 1 nivel, nunca domina el
# pilar por si solo (a diferencia de lesiones/closer, que pueden mover
# hasta 2).
_FIELDING_PCT_UNIT = 0.006
_FIELDING_LEVEL_CAP = 1


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

    fielding_note = ""
    if snapshot.home_fielding_pct is not None and snapshot.away_fielding_pct is not None:
        fielding_diff = snapshot.home_fielding_pct - snapshot.away_fielding_pct
        defense_level = max(-_FIELDING_LEVEL_CAP, min(_FIELDING_LEVEL_CAP, round(fielding_diff / _FIELDING_PCT_UNIT)))
        if defense_level != 0:
            level = max(-2, min(2, level + defense_level))
            fielding_note = (
                f" Fielding%: home={snapshot.home_fielding_pct:.3f}, away={snapshot.away_fielding_pct:.3f}."
            )

    explanation = (
        f"Lesiones clave: home={len(snapshot.home_key_injuries)}, away={len(snapshot.away_key_injuries)}."
        f"{closer_note}{fielding_note} -> advantage={level:+d}."
    )
    return PillarAdvantage(
        pillar="team_quality", advantage=level, explanation=explanation,
        pillar_contract_version=PILLAR_CONTRACT_VERSIONS["team_quality"],
    )
