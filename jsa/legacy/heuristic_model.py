"""Modelo heuristico ERA/OPS -- portado tal cual de
`mlb_edge_analyzer.v2/model/probability.py`. Ver `jsa/legacy/README.md`
antes de usar nada de este modulo: es una rama secundaria de
benchmarking, nunca el motor primario de JSA."""

from __future__ import annotations


def pitcher_score(era: float) -> float:
    """Entre mas bajo el ERA, mas alto el score. Tope en ERA=6 (score=0)."""
    return max(0, 6 - era) / 6


def hitter_score(ops: float, league_ops: float = 0.750) -> float:
    """OPS relativo al promedio de liga, con tope de 1.3 para evitar outliers."""
    return min(1.3, ops / league_ops)


def command_adjustment(
    k_pct: float | None, bb_pct: float | None, league_k_pct: float = 0.22, league_bb_pct: float = 0.08
) -> float:
    if k_pct is None or bb_pct is None:
        return 0.0
    k_diff = k_pct - league_k_pct
    bb_diff = league_bb_pct - bb_pct
    return (k_diff + bb_diff) * 0.5


def fatigue_adjustment(days_rest: int | None, last_outing_pitches: int | None) -> float:
    if days_rest is None or last_outing_pitches is None:
        return 0.0
    if days_rest < 4 and last_outing_pitches > 95:
        return -0.02
    return 0.0


def park_weather_adjustment(park_factor: float = 1.0, temp_f: float | None = None) -> float:
    adj = (park_factor - 1.0) * 0.05
    if temp_f is not None:
        adj += (temp_f - 70) * 0.0005
    return adj


def pitching_staff_score(starter_era: float, bullpen_era: float, starter_weight: float = 0.65) -> float:
    starter = pitcher_score(starter_era)
    bullpen = pitcher_score(bullpen_era)
    return starter_weight * starter + (1 - starter_weight) * bullpen


def model_prob(
    p_era: float, team_ops: float, league_ops: float = 0.750, bullpen_era: float | None = None,
    starter_weight: float = 0.65, k_pct: float | None = None, bb_pct: float | None = None,
    days_rest: int | None = None, last_outing_pitches: int | None = None,
    park_factor: float = 1.0, temp_f: float | None = None,
) -> float:
    """Probabilidad cruda (sin normalizar contra el rival) de que gane un
    equipo. Punto de partida sabermetrico estandar -- el barrido de
    calibracion de 4 temporadas encontro que este modelo, a diferencia de
    Skellam/NegBin, ya estaba bien calibrado de fabrica (ver
    jsa/legacy/README.md)."""
    if bullpen_era is not None:
        p = pitching_staff_score(p_era, bullpen_era, starter_weight)
    else:
        p = pitcher_score(p_era)

    h = hitter_score(team_ops, league_ops)
    base = (p + h) / 2
    base += command_adjustment(k_pct, bb_pct)
    base += fatigue_adjustment(days_rest, last_outing_pitches)
    base += park_weather_adjustment(park_factor, temp_f)
    return min(max(base, 0.05), 0.95)


def normalize_matchup(away_prob: float, home_prob: float, home_field_adv: float = 0.0) -> tuple[float, float]:
    """Normaliza las dos probabilidades crudas para que sumen 1.
    `home_field_adv` se suma a la probabilidad cruda del local ANTES de
    normalizar (ej. 0.02 = ventaja de jugar en casa)."""
    home_prob = home_prob + home_field_adv
    total = away_prob + home_prob
    if total == 0:
        return 0.5, 0.5
    return away_prob / total, home_prob / total
