from model.markets import run_line_prob, totals_prob, fair_total_line
from model.runs_projection import project_f5_runs


def test_run_line_probabilities_sum_to_exactly_one():
    home, away = run_line_prob(mu_home=4.5, mu_away=3.8)
    assert abs((home + away) - 1.0) < 1e-9


def test_run_line_probabilities_in_valid_range():
    home, away = run_line_prob(mu_home=4.5, mu_away=3.8)
    assert 0.0 <= home <= 1.0
    assert 0.0 <= away <= 1.0


def test_run_line_favors_stronger_team():
    home, away = run_line_prob(mu_home=6.0, mu_away=3.0)
    assert home > away


def test_totals_probabilities_sum_to_exactly_one():
    over, under = totals_prob(mu_home=4.5, mu_away=3.8, line=8.5)
    assert abs((over + under) - 1.0) < 1e-9


def test_totals_probabilities_in_valid_range():
    over, under = totals_prob(mu_home=4.5, mu_away=3.8, line=8.5)
    assert 0.0 <= over <= 1.0
    assert 0.0 <= under <= 1.0


def test_totals_low_line_favors_over():
    over, _ = totals_prob(mu_home=4.5, mu_away=3.8, line=3.5)
    assert over > 0.9


def test_totals_high_line_favors_under():
    over, under = totals_prob(mu_home=4.5, mu_away=3.8, line=15.5)
    assert under > 0.9


def test_fair_total_line_equals_sum_of_projections():
    assert fair_total_line(4.5, 3.8) == 8.3


def test_f5_runs_are_less_than_full_game_runs():
    f5 = project_f5_runs(team_ops=0.750, opp_starter_era=4.0, league_ops=0.750)
    assert 0 < f5 < 5.0


def test_f5_runs_floor_prevents_zero():
    f5 = project_f5_runs(team_ops=0.300, opp_starter_era=1.0, league_ops=0.750)
    assert f5 >= 0.2