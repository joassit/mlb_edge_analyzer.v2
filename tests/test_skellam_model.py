from model.skellam_model import skellam_win_prob


def test_complementary_probabilities_sum_to_exactly_one():
    p_a = skellam_win_prob(4.5, 3.8)
    p_b = skellam_win_prob(3.8, 4.5)
    assert abs((p_a + p_b) - 1.0) < 1e-9


def test_even_matchup_is_near_50_50():
    p = skellam_win_prob(4.4, 4.4)
    assert 0.48 < p < 0.52


def test_stronger_team_is_favored():
    p = skellam_win_prob(6.0, 3.0)
    assert p > 0.8
