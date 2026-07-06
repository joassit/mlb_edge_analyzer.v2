from scipy.stats import poisson

from model.negbin_model import negbin_win_prob, negbin_run_line_prob, negbin_totals_prob
from model.skellam_model import skellam_win_prob


def test_complementary_probabilities_sum_to_exactly_one():
    p_a = negbin_win_prob(4.5, 3.8, k=7.0)
    p_b = negbin_win_prob(3.8, 4.5, k=7.0)
    assert abs((p_a + p_b) - 1.0) < 1e-9


def test_probability_in_valid_range():
    p = negbin_win_prob(4.5, 3.8, k=7.0)
    assert 0.0 <= p <= 1.0


def test_even_matchup_is_near_50_50():
    p = negbin_win_prob(4.4, 4.4, k=7.0)
    assert 0.48 < p < 0.52


def test_stronger_team_is_favored():
    p = negbin_win_prob(6.0, 3.0, k=7.0)
    assert p > 0.8


def test_negbin_converges_to_skellam_as_k_grows():
    # k -> infinito hace que NB2 converja a Poisson (varianza = mu + mu^2/k
    # -> mu), así que negbin_win_prob con k enorme debe coincidir con
    # skellam_win_prob para el mismo par de medias -- esta es la prueba que
    # protege la conversión de (mu, k) a los parámetros (n, p) de
    # scipy.stats.nbinom, que es donde toda implementación de NB2 se
    # equivoca (confundir n con mu, o p con 1-p).
    mu_team, mu_opp = 4.5, 3.8
    p_negbin = negbin_win_prob(mu_team, mu_opp, k=1e9)
    p_skellam = skellam_win_prob(mu_team, mu_opp)
    assert abs(p_negbin - p_skellam) < 1e-3


def test_negbin_converges_to_skellam_for_lopsided_matchup():
    mu_team, mu_opp = 6.0, 3.0
    p_negbin = negbin_win_prob(mu_team, mu_opp, k=1e9)
    p_skellam = skellam_win_prob(mu_team, mu_opp)
    assert abs(p_negbin - p_skellam) < 1e-3


def test_run_line_probabilities_sum_to_exactly_one():
    home, away = negbin_run_line_prob(mu_home=4.5, mu_away=3.8, k=7.0)
    assert abs((home + away) - 1.0) < 1e-9


def test_run_line_favors_stronger_team():
    home, away = negbin_run_line_prob(mu_home=6.0, mu_away=3.0, k=7.0)
    assert home > away


def test_totals_probabilities_sum_to_exactly_one():
    over, under = negbin_totals_prob(mu_home=4.5, mu_away=3.8, k=7.0, line=8.5)
    assert abs((over + under) - 1.0) < 1e-9


def test_totals_probabilities_in_valid_range():
    over, under = negbin_totals_prob(mu_home=4.5, mu_away=3.8, k=7.0, line=8.5)
    assert 0.0 <= over <= 1.0
    assert 0.0 <= under <= 1.0


def test_small_k_gives_fatter_tail_than_poisson_for_totals():
    # La razón de existir del Binomial Negativo: con dispersión chica (k=5),
    # la probabilidad de un total alto (cola gorda) debe ser MAYOR que bajo
    # Poisson con las mismas medias -- si esto no se cumple, el modelo no
    # está capturando la sobredispersión que se le pidió.
    mu_home, mu_away = 4.5, 3.8
    over_negbin, _ = negbin_totals_prob(mu_home, mu_away, k=5, line=12.5)

    mu_total = mu_home + mu_away
    over_poisson = 1.0 - poisson.cdf(12, mu_total)

    assert over_negbin > over_poisson


def test_large_k_totals_converge_to_poisson():
    mu_home, mu_away = 4.5, 3.8
    over_negbin, _ = negbin_totals_prob(mu_home, mu_away, k=1e9, line=8.5)

    mu_total = mu_home + mu_away
    over_poisson = 1.0 - poisson.cdf(8, mu_total)

    assert abs(over_negbin - over_poisson) < 1e-3
