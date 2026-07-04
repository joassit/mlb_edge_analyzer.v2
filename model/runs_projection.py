"""
Proyección de carreras esperadas por equipo, usando SOLO datos que ya
obtenemos de fuentes gratuitas reales (nada de xFIP/wRC+ inventado sin fuente).

La idea: convertir OPS ofensivo + ERA del pitcheo rival + park factor en un
número de carreras esperadas (mu), para después usar una distribución de
Skellam y obtener una probabilidad de victoria estadísticamente fundamentada
(en vez del promedio simple pitcher_score/hitter_score).
"""

LEAGUE_AVG_RUNS_PER_GAME = 4.4  # aproximado, temporada moderna de MLB
LEAGUE_AVG_ERA = 4.30
HOME_FIELD_RUNS_BONUS = 0.15  # carreras extra esperadas por jugar en casa


def project_team_runs(team_ops: float, opp_starter_era: float, opp_bullpen_era: float,
                       league_ops: float = 0.750, league_era: float = LEAGUE_AVG_ERA,
                       park_factor: float = 1.0, starter_weight: float = 0.65,
                       is_home: bool = False) -> float:
    """
    Carreras esperadas de UN equipo, dado:
    - su propio OPS ofensivo
    - el ERA del abridor Y bullpen RIVAL (entre más alto, más fácil anotarle)
    - el park factor del estadio (afecta a ambas ofensivas por igual)
    """
    offense_factor = team_ops / league_ops
    opp_pitching_era = starter_weight * opp_starter_era + (1 - starter_weight) * opp_bullpen_era
    pitching_factor = opp_pitching_era / league_era  # rival débil (ERA alto) => anotamos más

    runs = LEAGUE_AVG_RUNS_PER_GAME * offense_factor * pitching_factor * park_factor
    if is_home:
        runs += HOME_FIELD_RUNS_BONUS

    return max(runs, 0.3)  # piso para evitar mu=0 (rompe la distribución de Poisson/Skellam)
def project_f5_runs(team_ops: float, opp_starter_era: float,
                     league_ops: float = 0.750, league_era: float = LEAGUE_AVG_ERA,
                     park_factor: float = 1.0, is_home: bool = False) -> float:
    """
    Carreras esperadas SOLO en las primeras 5 entradas (mercado F5).

    A propósito usa SOLO el ERA del abridor, sin mezclar bullpen — en las
    primeras 5 entradas normalmente sigue el abridor, el bullpen entra
    después. Escala la proyección de 9 a 5 entradas (5/9).
    """
    offense_factor = team_ops / league_ops
    pitching_factor = opp_starter_era / league_era

    full_game_runs = LEAGUE_AVG_RUNS_PER_GAME * offense_factor * pitching_factor * park_factor
    f5_runs = full_game_runs * (5 / 9)

    if is_home:
        f5_runs += HOME_FIELD_RUNS_BONUS * (5 / 9)

    return max(f5_runs, 0.2)