"""Modulo de Carreras Proyectadas y Handicap -- Seccion 9 del spec JSA v3.0.

Modulo INDEPENDIENTE, no un octavo pilar (Seccion 7.1). Dos usos: soporte
para Totales/Run Line, y senal de consistencia adicional para Moneyline
(Seccion 9.3) -- nunca se mezcla directamente con el Evidence Score sin
pasar por ese chequeo.

`variance_validated=False` siempre en esta entrega: la Seccion 9.2 exige
validar explicitamente la desviacion estandar del margen proyectado contra
la real antes de considerar el modulo apto para produccion -- sin
historial acumulado propio, esa validacion no existe todavia (ver
ROADMAP), asi que el modulo se calcula y se expone pero se marca como no
apto para decision, igual que la calibracion de 8.4.1."""

from __future__ import annotations

import math

from scipy.stats import skellam

from jsa.config import HOME_FIELD_RUNS_BONUS, LEAGUE_AVG_ERA, LEAGUE_AVG_RUNS_PER_GAME, STARTER_WEIGHT_IN_PITCHING
from jsa.domain.models import GameSnapshot, ProjectedRunsOutput
from jsa.engine.pillars.base import offense_factor

_LEAGUE_OPS_FALLBACK = 0.750


def _project_team_runs(
    team_ops: float, opp_starter_era: float, opp_bullpen_era: float, *, league_ops: float, league_era: float,
    park_factor: float, is_home: bool, league_avg_runs_per_game: float,
) -> float:
    """Mismo modelo multiplicativo que `mlb_edge_analyzer.v2/model/
    runs_projection.py::project_team_runs`, sin los ajustes de
    park_factor_weight/weather_correction (en el proyecto viejo ambos
    estaban neutralizados a 1.0/0.0 por falta de backtest -- se omiten
    aqui en vez de cargar un parametro que nunca hace nada)."""
    off_factor = offense_factor(team_ops, league_ops)
    opp_pitching_era = STARTER_WEIGHT_IN_PITCHING * opp_starter_era + (1 - STARTER_WEIGHT_IN_PITCHING) * opp_bullpen_era
    pitching_factor = opp_pitching_era / league_era if league_era > 0 else 1.0

    runs = league_avg_runs_per_game * off_factor * pitching_factor * park_factor
    if is_home:
        runs += HOME_FIELD_RUNS_BONUS
    return max(runs, 0.3)


def compute_projected_runs(snapshot: GameSnapshot) -> ProjectedRunsOutput:
    league_era = snapshot.league_avg_era or LEAGUE_AVG_ERA
    league_ops = snapshot.league_avg_ops or _LEAGUE_OPS_FALLBACK
    league_rpg = snapshot.league_avg_runs_per_game or LEAGUE_AVG_RUNS_PER_GAME
    park_factor = snapshot.park_factor or 1.0

    home_ops = snapshot.home_ops if snapshot.home_ops is not None else league_ops
    away_ops = snapshot.away_ops if snapshot.away_ops is not None else league_ops
    away_starter_era = snapshot.away_starter_xera if snapshot.away_starter_xera is not None else league_era
    home_starter_era = snapshot.home_starter_xera if snapshot.home_starter_xera is not None else league_era
    home_bullpen_era = snapshot.home_bullpen_era if snapshot.home_bullpen_era is not None else league_era
    away_bullpen_era = snapshot.away_bullpen_era if snapshot.away_bullpen_era is not None else league_era

    # Las carreras de HOME dependen del pitcheo de AWAY (y viceversa).
    mu_home = _project_team_runs(
        home_ops, away_starter_era, away_bullpen_era, league_ops=league_ops, league_era=league_era,
        park_factor=park_factor, is_home=True, league_avg_runs_per_game=league_rpg,
    )
    mu_away = _project_team_runs(
        away_ops, home_starter_era, home_bullpen_era, league_ops=league_ops, league_era=league_era,
        park_factor=park_factor, is_home=False, league_avg_runs_per_game=league_rpg,
    )

    sigma_margin = math.sqrt(max(mu_home, 0.05) + max(mu_away, 0.05))
    projected_margin = mu_home - mu_away

    prob_cover_handicap = None
    try:
        prob_cover_handicap = 1.0 - float(skellam.cdf(0, max(mu_home, 0.05), max(mu_away, 0.05)))
    except (ValueError, ZeroDivisionError):
        prob_cover_handicap = None

    return ProjectedRunsOutput(
        mu_home=mu_home,
        mu_away=mu_away,
        sigma_margin=sigma_margin,
        prob_cover_handicap=prob_cover_handicap,
        projected_margin=projected_margin,
        consistency_flag=None,  # se resuelve en compute_consistency_flag() contra el Evidence Score
        variance_validated=False,
    )


def skellam_win_prob(mu_team: float, mu_opponent: float) -> float:
    """Probabilidad de que `mu_team` gane sobre `mu_opponent`, via Skellam
    (diferencia de dos Poisson) + renormalizacion excluyendo el empate (un
    juego de MLB real no puede terminar empatado, pero Skellam en 9
    entradas si le asigna probabilidad a un empate). Mismo criterio que
    `mlb_edge_analyzer.v2/model/skellam_model.py::skellam_win_prob`.

    Nota: esta es una probabilidad CRUDA, no calibrada -- ver
    `CalibrationInfo.calibration_status`, nunca se usa para decision (8.4)
    sin pasar primero por calibracion obligatoria (8.4.1)."""
    mu_team = max(mu_team, 0.05)
    mu_opponent = max(mu_opponent, 0.05)
    prob_win = 1.0 - float(skellam.cdf(0, mu_team, mu_opponent))
    prob_loss = float(skellam.cdf(-1, mu_team, mu_opponent))
    denom = prob_win + prob_loss
    return (prob_win / denom) if denom > 0 else 0.5


def compute_consistency_flag(evidence_score: float, projected_margin: float) -> str:
    """Seccion 9.3."""
    evidence_sign = 0 if evidence_score == 0 else (1 if evidence_score > 0 else -1)
    margin_sign = 0 if projected_margin == 0 else (1 if projected_margin > 0 else -1)
    if evidence_sign == 0 or margin_sign == 0 or evidence_sign == margin_sign:
        return "aligned"
    return "conflicting"
