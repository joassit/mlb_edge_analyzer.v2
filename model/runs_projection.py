"""
Proyección de carreras esperadas por equipo.
"""
from config import PARK_FACTOR_WEIGHT, WEATHER_CORRECTION
from model.adjustments import offense_factor as _offense_factor

LEAGUE_AVG_RUNS_PER_GAME = 4.4
LEAGUE_AVG_ERA = 4.30
HOME_FIELD_RUNS_BONUS = 0.15

def decompose_team_runs_projection(team_ops: float, opp_starter_era: float, opp_bullpen_era: float,
                      league_ops: float = 0.750, league_era: float = LEAGUE_AVG_ERA,
                      park_factor: float = 1.0, starter_weight: float = 0.65,
                      is_home: bool = False, temp_f: float = None,
                      park_factor_weight: float = PARK_FACTOR_WEIGHT,
                      weather_correction: float = WEATHER_CORRECTION,
                      league_avg_runs_per_game: float = LEAGUE_AVG_RUNS_PER_GAME) -> dict:
    """
    Carreras esperadas de UN equipo, descompuestas en la contribución de
    cada factor (ofensa/pitcheo rival/parque/clima/local) -- para auditar
    UN pick puntual (p.ej. uno que perdió) y ver qué insumo pesó más, en
    vez de solo ver el número final.

    `project_team_runs()` es un envoltorio delgado sobre esta función (ver
    abajo) para que el número final y su descomposición NUNCA puedan
    desincronizarse -- un cambio en la fórmula solo se edita acá.

    La descomposición es un "waterfall" secuencial (ofensa -> pitcheo rival
    -> parque -> clima -> bono de local), no una descomposición log-aditiva
    ni "aislar un factor mantieniendo los demás fijos": cada paso multiplica
    el acumulado del paso anterior, así que la suma de las 5 contribuciones
    MÁS la base de liga da exactamente el total (antes del piso de 0.3
    carreras) -- a diferencia de aislar factores uno por uno, que dejaría
    un residuo de interacción sin explicar. La contrapartida es que el
    reparto depende del ORDEN elegido (multiplicar no conmuta con "cuánto
    aportó cada quien"); se usa el mismo orden en que la fórmula original
    ya calcula cada factor, no un orden arbitrario.
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

    step0_base = league_avg_runs_per_game
    step1_ofensa = step0_base * offense_factor
    step2_pitcheo = step1_ofensa * pitching_factor
    step3_parque = step2_pitcheo * weighted_park_factor
    step4_clima = step3_parque * (1.0 + weather_impact)
    local_bonus = HOME_FIELD_RUNS_BONUS if is_home else 0.0
    step5_final = step4_clima + local_bonus

    final_runs = max(step5_final, 0.3)

    return {
        "final_runs": final_runs,
        # True si el piso de 0.3 alteró el resultado -- si es así, las
        # contribuciones de abajo siguen sumando a step5_final, NO a
        # final_runs, y el waterfall no debe leerse como "exacto" en ese caso.
        "floored": final_runs != step5_final,
        "base_liga_contrib": step0_base,
        "ofensa_contrib": step1_ofensa - step0_base,
        "pitcheo_rival_contrib": step2_pitcheo - step1_ofensa,
        "parque_contrib": step3_parque - step2_pitcheo,
        "clima_contrib": step4_clima - step3_parque,
        "local_contrib": local_bonus,
    }


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

    Envoltorio delgado sobre decompose_team_runs_projection() -- ver ahí
    para la descomposición por componente (ofensa/pitcheo/parque/clima/local).
    """
    return decompose_team_runs_projection(
        team_ops, opp_starter_era, opp_bullpen_era, league_ops, league_era,
        park_factor, starter_weight, is_home, temp_f,
        park_factor_weight, weather_correction, league_avg_runs_per_game,
    )["final_runs"]

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