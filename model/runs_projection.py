"""
Proyección de carreras esperadas por equipo.
"""
from config import PARK_FACTOR_WEIGHT, WEATHER_CORRECTION

LEAGUE_AVG_RUNS_PER_GAME = 4.4
LEAGUE_AVG_ERA = 4.30
HOME_FIELD_RUNS_BONUS = 0.15

def project_team_runs(team_ops: float, opp_starter_era: float, opp_bullpen_era: float,
                      league_ops: float = 0.750, league_era: float = LEAGUE_AVG_ERA,
                      park_factor: float = 1.0, starter_weight: float = 0.65,
                      is_home: bool = False, temp_f: float = None) -> float:
    """
    Carreras esperadas de UN equipo con ajuste de peso para el factor de parque.
    """
    offense_factor = team_ops / league_ops
    opp_pitching_era = starter_weight * opp_starter_era + (1 - starter_weight) * opp_bullpen_era
    pitching_factor = opp_pitching_era / league_era

    # AJUSTE: Aplicamos el peso al factor de parque (PARK_FACTOR_WEIGHT)
    # Si el estadio es 1.05 (5% más carreras), con peso 1.15 ahora será 5.75% más carreras.
    weighted_park_factor = 1.0 + ((park_factor - 1.0) * PARK_FACTOR_WEIGHT)
    
    # AJUSTE: Corrección por clima extremo (>85°F)
    weather_impact = 0.0
    if temp_f and temp_f > 85:
        weather_impact = WEATHER_CORRECTION

    runs = LEAGUE_AVG_RUNS_PER_GAME * offense_factor * pitching_factor * weighted_park_factor
    
    # Sumar impacto del clima al total
    runs += (runs * weather_impact)
    
    if is_home:
        runs += HOME_FIELD_RUNS_BONUS

    return max(runs, 0.3)

def project_f5_runs(team_ops: float, opp_starter_era: float,
                    league_ops: float = 0.750, league_era: float = LEAGUE_AVG_ERA,
                    park_factor: float = 1.0, is_home: bool = False) -> float:
    # Nota: Aquí no aplicamos WEATHER_CORRECTION agresiva porque F5 suele ser más corta
    # pero puedes aplicar el PARK_FACTOR_WEIGHT si deseas mayor sensibilidad.
    offense_factor = team_ops / league_ops
    pitching_factor = opp_starter_era / league_era

    weighted_park_factor = 1.0 + ((park_factor - 1.0) * PARK_FACTOR_WEIGHT)

    full_game_runs = LEAGUE_AVG_RUNS_PER_GAME * offense_factor * pitching_factor * weighted_park_factor
    f5_runs = full_game_runs * (5 / 9)

    if is_home:
        f5_runs += HOME_FIELD_RUNS_BONUS * (5 / 9)

    return max(f5_runs, 0.2)