from model.markets import run_line_prob, totals_prob, fair_total_line
from model.runs_projection import project_f5_runs


# --- Criterios de aceptación: rango válido y complementariedad exacta ---

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
    # una linea muy baja (ej. 3.5 cuando se esperan 8+ carreras) casi
    # seguro termina en Over
    over, _ = totals_prob(mu_home=4.5, mu_away=3.8, line=3.5)
    assert over > 0.9


def test_totals_high_line_favors_under():
    over, under = totals_prob(mu_home=4.5, mu_away=3.8, line=15.5)
    assert under > 0.9


# --- A3: push en totales con línea ENTERA (total == line) ---
# Antes, under_prob = poisson.cdf(floor(line), mu_total) incluía P(total ==
# line) como si fuera "under" -- con línea 8.0, ese empate es un push real
# (Pick._resolve_pick_outcome ya lo trata como push), no under.

def test_totals_integer_line_excludes_push_from_over_and_under():
    from scipy.stats import poisson
    mu_home, mu_away = 4.0, 4.5
    mu_total = mu_home + mu_away
    line = 8.0

    over, under = totals_prob(mu_home, mu_away, line)

    push_prob = poisson.pmf(int(line), mu_total)
    under_raw = poisson.cdf(int(line) - 1, mu_total)  # P(total < line), sin el empate
    over_raw = 1.0 - poisson.cdf(int(line), mu_total)  # P(total > line)
    remaining = 1.0 - push_prob

    assert abs(under - (under_raw / remaining)) < 1e-9
    assert abs(over - (over_raw / remaining)) < 1e-9
    assert abs((over + under) - 1.0) < 1e-9


def test_totals_half_point_line_unaffected_by_push_exclusion():
    # Con línea X.5 el empate es matemáticamente imposible -- debe dar
    # exactamente lo mismo que antes del fix de A3.
    over, under = totals_prob(mu_home=4.5, mu_away=3.8, line=8.5)
    assert abs((over + under) - 1.0) < 1e-9

    from scipy.stats import poisson
    mu_total = 4.5 + 3.8
    expected_under = poisson.cdf(8, mu_total)
    assert abs(under - expected_under) < 1e-9


def test_fair_total_line_equals_sum_of_projections():
    assert fair_total_line(4.5, 3.8) == 8.3


def test_f5_runs_are_less_than_full_game_runs():
    f5 = project_f5_runs(team_ops=0.750, opp_starter_era=4.0, league_ops=0.750)
    assert 0 < f5 < 5.0  # deben ser menos que un juego completo (~9 entradas)


def test_f5_runs_floor_prevents_zero():
    f5 = project_f5_runs(team_ops=0.300, opp_starter_era=1.0, league_ops=0.750)
    assert f5 >= 0.2
