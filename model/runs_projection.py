"""
Proyección de carreras esperadas por equipo.
"""
from config import PARK_FACTOR_WEIGHT, WEATHER_CORRECTION
from model.adjustments import offense_factor as _offense_factor

LEAGUE_AVG_RUNS_PER_GAME = 4.4
LEAGUE_AVG_ERA = 4.30
HOME_FIELD_RUNS_BONUS = 0.15

def project_team_runs(team_ops: float, opp_starter_era: float, opp_bullpen_era: float,
                      league_ops: float = 0.750, league_era: float = LEAGUE_AVG_ERA,
                      park_factor: float = 1.0, starter_weight: float = 0.65,
                      is_home: bool = False, temp_f: float = None,
                      park_factor_weight: float = PARK_FACTOR_WEIGHT,
                      weather_correction: float = WEATHER_CORRECTION,
                      league_avg_runs_per_game: float = LEAGUE_AVG_RUNS_PER_GAME) -> float:
    """
    Carreras esperadas de UN equipo con ajuste de peso para el factor de parque.

    `park_factor_weight`/`weather_correction`/`league_avg_runs_per_game` son
    parámetros (no solo globals de config/módulo) para que un
    FeatureSnapshot congelado pueda recalcularse con los valores vigentes
    AL MOMENTO de la predicción original, aunque esos valores cambien
    después -- ver model/predictor.py.
    """
    offense_factor = _offense_factor(team_ops, league_ops)
    opp_pitching_era = starter_weight * opp_starter_era + (1 - starter_weight) * opp_bullpen_era
    pitching_factor = opp_pitching_era / league_era

    # AJUSTE: Aplicamos el peso al factor de parque (park_factor_weight)
    # Si el estadio es 1.05 (5% más carreras), con peso 1.15 ahora será 5.75% más carreras.
    weighted_park_factor = 1.0 + ((park_factor - 1.0) * park_factor_weight)

    # AJUSTE: Corrección por clima extremo (>85°F)
    weather_impact = 0.0
    if temp_f and temp_f > 85:
        weather_impact = weather_correction

    runs = league_avg_runs_per_game * offense_factor * pitching_factor * weighted_park_factor
    
    # Sumar impacto del clima al total
    runs += (runs * weather_impact)
    
    if is_home:
        runs += HOME_FIELD_RUNS_BONUS

    return max(runs, 0.3)

def project_f5_runs(team_ops: float, opp_starter_era: float,
                    league_ops: float = 0.750, league_era: float = LEAGUE_AVG_ERA,
                    park_factor: float = 1.0, is_home: bool = False,
                    park_factor_weight: float = PARK_FACTOR_WEIGHT) -> float:
    # No conectado al pipeline todavía (uso futuro declarado).
    # Nota: Aquí no aplicamos weather_correction agresiva porque F5 suele ser más corta
    # pero puedes aplicar el park_factor_weight si deseas mayor sensibilidad.
    offense_factor = _offense_factor(team_ops, league_ops)
    pitching_factor = opp_starter_era / league_era

    weighted_park_factor = 1.0 + ((park_factor - 1.0) * park_factor_weight)

    full_game_runs = LEAGUE_AVG_RUNS_PER_GAME * offense_factor * pitching_factor * weighted_park_factor
    f5_runs = full_game_runs * (5 / 9)

    if is_home:
        f5_runs += HOME_FIELD_RUNS_BONUS * (5 / 9)

    return max(f5_runs, 0.2)