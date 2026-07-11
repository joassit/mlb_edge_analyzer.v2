import pytest

from model.edge import (
    implied_prob, fair_odds, edge, expected_value, no_vig_probs, market_favorite,
    power_devig,
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


# --- M4: power_devig() -- referencia secundaria, no reemplaza a no_vig_probs ---

def test_power_devig_sums_to_one():
    fav, dog = power_devig(-250, 210)
    assert abs((fav + dog) - 1.0) < 1e-9


def test_power_devig_matches_proportional_on_symmetric_market():
    # -110/-110: ambos lados con la misma cuota -- power y proporcional
    # deben coincidir (no hay asimetría que el método power corrija).
    power_a, power_b = power_devig(-110, -110)
    prop_a, prop_b = no_vig_probs(-110, -110)
    assert abs(power_a - prop_a) < 1e-6
    assert abs(power_b - prop_b) < 1e-6


def test_power_devig_assigns_less_probability_to_underdog_than_proportional():
    # -250 favorito / +210 underdog: el método power debe asignarle MENOS
    # probabilidad al underdog que el reparto proporcional de no_vig_probs.
    power_fav, power_dog = power_devig(-250, 210)
    prop_fav, prop_dog = no_vig_probs(-250, 210)
    assert power_dog < prop_dog
    assert power_fav > prop_fav


def test_power_devig_returns_pickem_when_market_has_no_real_vig():
    # p_a + p_b <= 1 (cuotas sin margen real, ej. de prueba) -- nada que quitar.
    fav, dog = power_devig(100, 100)  # implied_prob(100)=0.5 c/u, suma exacta 1.0
    assert abs(fav - 0.5) < 1e-9
    assert abs(dog - 0.5) < 1e-9


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
