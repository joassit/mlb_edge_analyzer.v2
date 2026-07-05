"""
Pruebas de model/predictor.py — el punto único de cálculo que usa tanto
main.py en vivo como cualquier recálculo histórico sobre un FeatureSnapshot
congelado. Si esto se rompe, se rompen los dos consumidores a la vez.
"""

from model.predictor import predict_from_raw_inputs


def _base_raw_inputs(**overrides) -> dict:
    raw = {
        "away_era": 3.00, "home_era": 4.50,
        "away_ops": 0.780, "home_ops": 0.740, "league_ops": 0.750,
        "league_era": 4.30,
        "away_bullpen_era": 4.30, "home_bullpen_era": 4.30,
        "away_k_pct": 0.25, "away_bb_pct": 0.08,
        "home_k_pct": 0.25, "home_bb_pct": 0.08,
        "away_days_rest": 5, "away_last_outing_pitches": 90,
        "home_days_rest": 5, "home_last_outing_pitches": 90,
        "park_factor": 1.0, "park_name": "Test Park",
        "temp_f": 72, "wind_mph": 5, "wind_direction_deg": 180,
        "market_price": None, "market_no_vig": None,
        "starter_weight": 0.65, "home_field_advantage": 0.02,
    }
    raw.update(overrides)
    return raw


def test_predict_from_raw_inputs_returns_complementary_probabilities():
    prediction = predict_from_raw_inputs(_base_raw_inputs())

    assert abs((prediction["away_model_prob"] + prediction["home_model_prob"]) - 1.0) < 1e-9
    assert abs((prediction["away_skellam_prob"] + prediction["home_skellam_prob"]) - 1.0) < 1e-9


def test_predict_from_raw_inputs_favors_the_better_team():
    # away tiene mejor ERA propio y mejor OPS propio -> debe ser favorito
    prediction = predict_from_raw_inputs(_base_raw_inputs())

    assert prediction["away_model_prob"] > 0.5
    assert prediction["away_skellam_prob"] > 0.5


def test_predict_from_raw_inputs_is_deterministic_given_the_same_inputs():
    raw = _base_raw_inputs()
    first = predict_from_raw_inputs(raw)
    second = predict_from_raw_inputs(raw)
    assert first == second


def test_predict_from_raw_inputs_defaults_league_era_when_missing():
    raw = _base_raw_inputs()
    del raw["league_era"]
    # No debe lanzar KeyError — debe caer a LEAGUE_AVG_ERA del propio módulo.
    prediction = predict_from_raw_inputs(raw)
    assert 0 < prediction["home_model_prob"] < 1
