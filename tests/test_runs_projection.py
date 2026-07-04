from model.runs_projection import project_team_runs


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
