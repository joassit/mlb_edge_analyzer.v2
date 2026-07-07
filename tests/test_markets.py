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


# --- C1: run_line con el VISITANTE como favorito (favorite_side="away") ---
# Antes, run_line_prob() siempre asumía que el LOCAL era favorito (-1.5) sin
# importar qué equipo tuviera el mu más alto -- con un visitante favorito
# real (~45% de los juegos), esto comparaba la cuota de una línea contra la
# probabilidad de la línea contraria.

def test_run_line_home_favorite_default_matches_no_favorite_side_arg():
    # favorite_side="home" (default explícito) debe dar EXACTAMENTE lo
    # mismo que no pasar el argumento -- compatibilidad hacia atrás.
    with_default = run_line_prob(mu_home=4.5, mu_away=3.8)
    with_explicit = run_line_prob(mu_home=4.5, mu_away=3.8, favorite_side="home")
    assert with_default == with_explicit


def test_run_line_away_favorite_probabilities_sum_to_one():
    home, away = run_line_prob(mu_home=3.0, mu_away=6.0, favorite_side="away")
    assert abs((home + away) - 1.0) < 1e-9


def test_run_line_away_favorite_covers_more_often_than_home_underdog():
    # Visitante muy superior (mu 6.0 vs 3.0) puesto como favorito -1.5 --
    # debe cubrir con más frecuencia que el local (underdog) su +1.5.
    home, away = run_line_prob(mu_home=3.0, mu_away=6.0, favorite_side="away")
    assert away > home


def test_run_line_home_fav_vs_away_fav_are_symmetric_with_mu_swapped():
    # "Visitante favorito con (mu_home=A, mu_away=B)" es matemáticamente la
    # misma pregunta que "local favorito con (mu_home=B, mu_away=A)" --
    # solo se intercambian los roles de quién tiene el mu más alto.
    home_away_fav, away_away_fav = run_line_prob(mu_home=3.0, mu_away=6.0, favorite_side="away")
    home_home_fav, away_home_fav = run_line_prob(mu_home=6.0, mu_away=3.0, favorite_side="home")
    assert abs(away_away_fav - home_home_fav) < 1e-9
    assert abs(home_away_fav - away_home_fav) < 1e-9


def test_run_line_rejects_invalid_favorite_side():
    import pytest
    with pytest.raises(ValueError):
        run_line_prob(mu_home=4.5, mu_away=3.8, favorite_side="visitor")


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