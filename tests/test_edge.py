import pytest

from model.edge import (
    implied_prob, fair_odds, edge, kelly_fraction, expected_value, no_vig_probs, market_favorite,
)


def test_implied_prob_favorite():
    assert abs(implied_prob(-150) - 0.6) < 0.001


def test_implied_prob_underdog():
    assert abs(implied_prob(150) - 0.4) < 0.001


def test_fair_odds_favorite_is_negative():
    assert fair_odds(0.6) < 0


def test_fair_odds_underdog_is_positive():
    assert fair_odds(0.4) > 0


def test_fair_odds_rejects_invalid_probabilities():
    with pytest.raises(ValueError):
        fair_odds(1.0)
    with pytest.raises(ValueError):
        fair_odds(0.0)


def test_edge_positive_when_model_more_confident_than_market():
    assert edge(0.60, 0.55) > 0
    assert edge(0.50, 0.55) < 0


def test_kelly_fraction_is_non_negative():
    assert kelly_fraction(model_p=0.5, odds=100, fraction=0.25) >= 0


def test_kelly_fraction_positive_when_real_edge_exists():
    assert kelly_fraction(model_p=0.65, odds=100, fraction=0.25) > 0


def test_expected_value_positive_with_real_edge():
    # modelo cree 65%, cuota +100 (pagaria 1:1) -> EV positivo claro
    assert expected_value(model_p=0.65, odds=100) > 0


def test_expected_value_negative_without_edge():
    # modelo cree 40%, cuota +100 (pagaria 1:1) -> EV negativo
    assert expected_value(model_p=0.40, odds=100) < 0


def test_expected_value_zero_at_fair_price():
    # si el modelo coincide exactamente con la cuota justa, EV ~ 0
    fair = fair_odds(0.60)
    assert abs(expected_value(model_p=0.60, odds=fair)) < 0.01


def test_no_vig_probs_sums_to_one():
    away_p, home_p = no_vig_probs(-135, 115)
    assert abs((away_p + home_p) - 1.0) < 1e-9


def test_no_vig_probs_removes_the_house_margin():
    # -110/-110 implica 0.524 + 0.524 = 1.048 de vig; sin vig debe quedar 0.5/0.5
    away_p, home_p = no_vig_probs(-110, -110)
    assert abs(away_p - 0.5) < 1e-9
    assert abs(home_p - 0.5) < 1e-9


def test_no_vig_probs_symmetric_regardless_of_order():
    away_p, home_p = no_vig_probs(-150, 130)
    home_p2, away_p2 = no_vig_probs(130, -150)
    assert abs(away_p - away_p2) < 1e-9
    assert abs(home_p - home_p2) < 1e-9


def test_market_favorite_picks_the_higher_probability_side():
    fav = market_favorite("Away Team", "Home Team", away_prob=0.62, home_prob=0.38)
    assert fav == {"team": "Away Team", "side": "away", "prob": 0.62, "pickem": False}


def test_market_favorite_picks_home_when_home_is_favored():
    fav = market_favorite("Away Team", "Home Team", away_prob=0.40, home_prob=0.60)
    assert fav["team"] == "Home Team"
    assert fav["side"] == "home"


def test_market_favorite_flags_pickem_when_probabilities_are_close():
    fav = market_favorite("Away Team", "Home Team", away_prob=0.505, home_prob=0.495)
    assert fav["pickem"] is True
    assert fav["team"] is None
    assert fav["side"] is None


def test_market_favorite_respects_custom_pickem_threshold():
    fav = market_favorite("Away Team", "Home Team", away_prob=0.53, home_prob=0.47, pickem_threshold=0.10)
    assert fav["pickem"] is True
