import pytest

from model.edge import implied_prob, fair_odds, edge, kelly_fraction, expected_value


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
