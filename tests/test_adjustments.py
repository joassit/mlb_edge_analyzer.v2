"""
Pruebas de model/adjustments.py: shrinkage de ERA por muestra chica,
factor ofensivo no lineal, y el alias devig_two_way.
"""

from model.adjustments import shrunk_era, offense_factor, devig_two_way
from model.edge import no_vig_probs


def test_shrunk_era_pulls_small_sample_toward_league_average():
    # Un ERA de 2.00 con solo 10 IP debería acercarse mucho más a la liga
    # que el mismo ERA con 150 IP.
    small_sample = shrunk_era(era=2.00, innings=10, league_era=4.30)
    large_sample = shrunk_era(era=2.00, innings=150, league_era=4.30)
    assert small_sample > large_sample
    # Con muestra grande se acerca mucho más al ERA real (2.00) que con
    # muestra chica, aunque no lo alcance exactamente (k_ip sigue pesando).
    assert (large_sample - 2.00) < (small_sample - 2.00) / 2


def test_shrunk_era_with_zero_innings_returns_league_average():
    assert shrunk_era(era=1.50, innings=0, league_era=4.30) == 4.30


def test_shrunk_era_matches_raw_era_as_innings_grow_to_infinity_direction():
    # A más entradas, más se acerca al ERA crudo (menos shrinkage relativo)
    era, league_era = 3.00, 4.30
    ip_50 = shrunk_era(era, 50, league_era)
    ip_500 = shrunk_era(era, 500, league_era)
    assert abs(ip_500 - era) < abs(ip_50 - era)


def test_offense_factor_is_one_at_league_average():
    assert abs(offense_factor(0.750, 0.750) - 1.0) < 1e-9


def test_offense_factor_amplifies_above_average_offense_nonlinearly():
    linear_ratio = 0.850 / 0.750
    amplified = offense_factor(0.850, 0.750)
    assert amplified > linear_ratio  # exponente > 1 amplifica por encima del ratio lineal


def test_offense_factor_dampens_below_average_offense_nonlinearly():
    linear_ratio = 0.650 / 0.750
    amplified = offense_factor(0.650, 0.750)
    assert amplified < linear_ratio  # por debajo de 1, el exponente lo empuja más abajo


def test_devig_two_way_is_the_same_function_as_no_vig_probs():
    assert devig_two_way is no_vig_probs
    assert devig_two_way(-150, 130) == no_vig_probs(-150, 130)
