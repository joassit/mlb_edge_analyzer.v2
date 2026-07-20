from model.runs_projection import (
    project_team_runs, decompose_team_runs_projection, LEAGUE_AVG_RUNS_PER_GAME, HOME_FIELD_RUNS_BONUS,
)


def test_strong_offense_vs_weak_pitching_projects_more_runs():
    strong = project_team_runs(0.800, 5.50, 4.80, league_ops=0.750, park_factor=1.0)
    weak = project_team_runs(0.680, 2.20, 2.90, league_ops=0.750, park_factor=1.0)
    assert strong > weak


def test_home_field_bonus_is_applied():
    away = project_team_runs(0.750, 4.0, 4.0, league_ops=0.750, park_factor=1.0, is_home=False)
    home = project_team_runs(0.750, 4.0, 4.0, league_ops=0.750, park_factor=1.0, is_home=True)
    assert home > away


def test_park_factor_scales_projected_runs():
    neutral = project_team_runs(0.750, 4.0, 4.0, league_ops=0.750, park_factor=1.0)
    hitter_park = project_team_runs(0.750, 4.0, 4.0, league_ops=0.750, park_factor=1.15)
    assert hitter_park > neutral


def test_floor_prevents_zero_runs():
    runs = project_team_runs(0.300, 1.0, 1.0, league_ops=0.750, park_factor=0.5)
    assert runs >= 0.3


def test_league_avg_runs_per_game_parameter_defaults_to_module_constant():
    default_call = project_team_runs(0.750, 4.0, 4.0, league_ops=0.750, park_factor=1.0)
    explicit_call = project_team_runs(0.750, 4.0, 4.0, league_ops=0.750, park_factor=1.0,
                                       league_avg_runs_per_game=LEAGUE_AVG_RUNS_PER_GAME)
    assert default_call == explicit_call


def test_higher_league_avg_runs_per_game_scales_projection_up():
    # A2: antes esto era una constante fija del módulo, imposible de
    # override -- ahora un entorno de carreras real más alto (ej. temporada
    # de mucha ofensiva) debe reflejarse directamente en la proyección.
    low_env = project_team_runs(0.750, 4.0, 4.0, league_ops=0.750, park_factor=1.0,
                                 league_avg_runs_per_game=4.4)
    high_env = project_team_runs(0.750, 4.0, 4.0, league_ops=0.750, park_factor=1.0,
                                  league_avg_runs_per_game=5.5)
    assert high_env > low_env


def test_project_team_runs_matches_decompose_final_runs():
    # project_team_runs() es un envoltorio delgado sobre
    # decompose_team_runs_projection() -- nunca pueden divergir en el
    # número final, solo uno expone además el desglose por componente.
    kwargs = dict(team_ops=0.780, opp_starter_era=3.9, opp_bullpen_era=4.1,
                  league_ops=0.750, park_factor=1.08, is_home=True, temp_f=90.0)
    assert project_team_runs(**kwargs) == decompose_team_runs_projection(**kwargs)["final_runs"]


def test_decompose_waterfall_sums_exactly_to_final_runs_when_not_floored():
    d = decompose_team_runs_projection(0.780, 3.9, 4.1, league_ops=0.750, park_factor=1.08,
                                        is_home=True, temp_f=90.0)
    assert not d["floored"]
    total = (d["base_liga_contrib"] + d["ofensa_contrib"] + d["pitcheo_rival_contrib"]
             + d["parque_contrib"] + d["clima_contrib"] + d["local_contrib"])
    assert total == d["final_runs"]


def test_decompose_local_contrib_matches_home_field_bonus():
    d = decompose_team_runs_projection(0.750, 4.0, 4.0, league_ops=0.750, park_factor=1.0, is_home=True)
    assert d["local_contrib"] == HOME_FIELD_RUNS_BONUS
    d_away = decompose_team_runs_projection(0.750, 4.0, 4.0, league_ops=0.750, park_factor=1.0, is_home=False)
    assert d_away["local_contrib"] == 0.0


def test_decompose_flags_floored_when_floor_alters_result():
    d = decompose_team_runs_projection(0.300, 1.0, 1.0, league_ops=0.750, park_factor=0.5)
    assert d["floored"]
    assert d["final_runs"] == 0.3


def test_decompose_no_weather_impact_below_85f():
    # weather_correction=0.05 explícito -- WEATHER_CORRECTION por defecto en
    # config.py es 0.0 (corrección desactivada en producción), así que un
    # weather_impact real solo se observa pasando un valor distinto de cero.
    d = decompose_team_runs_projection(0.750, 4.0, 4.0, league_ops=0.750, park_factor=1.0,
                                        temp_f=70.0, weather_correction=0.05)
    assert d["clima_contrib"] == 0.0
    d_hot = decompose_team_runs_projection(0.750, 4.0, 4.0, league_ops=0.750, park_factor=1.0,
                                            temp_f=95.0, weather_correction=0.05)
    assert d_hot["clima_contrib"] > 0.0
