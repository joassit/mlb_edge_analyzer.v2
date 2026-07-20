"""
Pruebas de model/predictor.py — el punto único de cálculo que usa tanto
main.py en vivo como cualquier recálculo histórico sobre un FeatureSnapshot
congelado. Si esto se rompe, se rompen los dos consumidores a la vez.
"""

from model.predictor import predict_from_raw_inputs, decompose_from_raw_inputs


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


def test_predict_from_raw_inputs_works_without_frozen_park_config_keys():
    # Snapshot capturado ANTES de la corrección V5 -- no trae
    # park_factor_weight/weather_correction. No debe lanzar KeyError.
    raw = _base_raw_inputs(park_factor=1.1)
    prediction = predict_from_raw_inputs(raw)
    assert 0 < prediction["home_model_prob"] < 1


def test_predict_from_raw_inputs_uses_frozen_park_factor_weight_not_live_config(monkeypatch):
    # Si config.PARK_FACTOR_WEIGHT cambia DESPUÉS de capturar un snapshot,
    # recalcular ese snapshot debe seguir usando el valor congelado en el
    # propio raw_inputs, no el valor actual de config.py -- si no, el
    # recálculo histórico ya no sería reproducible.
    import model.predictor as predictor_mod
    monkeypatch.setattr(predictor_mod, "PARK_FACTOR_WEIGHT", 5.0)

    raw_frozen = _base_raw_inputs(park_factor=1.1, park_factor_weight=1.0, weather_correction=0.0)
    raw_using_live_config = _base_raw_inputs(park_factor=1.1)  # sin override -- cae al config "actual"

    frozen_result = predict_from_raw_inputs(raw_frozen)
    live_config_result = predict_from_raw_inputs(raw_using_live_config)

    assert frozen_result["home_proj_runs"] != live_config_result["home_proj_runs"]


def test_predict_from_raw_inputs_run_line_probs_unaffected_by_market_run_line_favorite_side():
    # C1 agregó favorite_side a MARKET_SPREADS/Pick (model/picks.py,
    # db/database.py), pero predict_from_raw_inputs() NUNCA lee
    # raw["market_run_line"] -- home_covers_rl_prob/away_covers_rl_prob son
    # la línea justa informativa (siempre 1.5, home favorito), no la línea
    # real de mercado. Un snapshot viejo (sin "market_run_line") debe
    # recalcular EXACTAMENTE igual que uno nuevo con favorite_side="away"
    # y una línea distinta -- si no fuera así, C1 habría roto la regla de
    # compatibilidad hacia atrás de snapshots congelados antes del cambio.
    raw_old_snapshot = _base_raw_inputs()  # snapshot pre-C1, sin "market_run_line"
    raw_new_with_market_data = _base_raw_inputs(
        market_run_line={"line": 2.5, "favorite_side": "away", "home": -120, "away": 100}
    )

    old_result = predict_from_raw_inputs(raw_old_snapshot)
    new_result = predict_from_raw_inputs(raw_new_with_market_data)

    assert old_result["home_covers_rl_prob"] == new_result["home_covers_rl_prob"]
    assert old_result["away_covers_rl_prob"] == new_result["away_covers_rl_prob"]
    assert old_result == new_result  # snapshot completo idéntico, no solo el run line


def test_predict_from_raw_inputs_falls_back_to_league_avg_runs_per_game_constant_when_missing():
    # A2: raw["league_avg_runs_per_game"] es una clave NUEVA -- un snapshot
    # congelado antes de este cambio no la trae. Debe recalcular EXACTAMENTE
    # igual que un snapshot que sí trae la constante actual de forma
    # explícita (Regla 4 de compatibilidad hacia atrás).
    from model.runs_projection import LEAGUE_AVG_RUNS_PER_GAME

    raw_old_snapshot = _base_raw_inputs()
    assert "league_avg_runs_per_game" not in raw_old_snapshot

    raw_with_explicit_constant = _base_raw_inputs(league_avg_runs_per_game=LEAGUE_AVG_RUNS_PER_GAME)

    old_result = predict_from_raw_inputs(raw_old_snapshot)
    explicit_result = predict_from_raw_inputs(raw_with_explicit_constant)

    assert old_result == explicit_result


def test_predict_from_raw_inputs_unaffected_by_market_no_vig_power():
    # M4: market_no_vig_power es una clave NUEVA, puramente informativa --
    # predict_from_raw_inputs() nunca debe leerla. Un snapshot viejo (sin
    # ella) debe recalcular EXACTAMENTE igual que uno nuevo que sí la trae.
    raw_old_snapshot = _base_raw_inputs()
    assert "market_no_vig_power" not in raw_old_snapshot

    raw_with_power_devig = _base_raw_inputs(market_no_vig_power=(0.71, 0.29))

    old_result = predict_from_raw_inputs(raw_old_snapshot)
    new_result = predict_from_raw_inputs(raw_with_power_devig)

    assert old_result == new_result


def test_predict_from_raw_inputs_applies_skellam_shrinkage_from_frozen_alpha():
    # La contracción hacia 0.5 debe leer el alpha CONGELADO en el snapshot:
    # con alpha=1.0 (identidad) se recupera la probabilidad cruda, y la
    # calibrada debe ser exactamente 0.5 + alpha*(cruda - 0.5).
    raw_prob = predict_from_raw_inputs(_base_raw_inputs(skellam_shrinkage_alpha=1.0))["home_skellam_prob"]
    calibrated = predict_from_raw_inputs(_base_raw_inputs(skellam_shrinkage_alpha=0.5))["home_skellam_prob"]

    assert abs(calibrated - (0.5 + 0.5 * (raw_prob - 0.5))) < 1e-12
    # La contracción acerca a 0.5, nunca cruza al otro lado del favorito.
    assert (raw_prob - 0.5) * (calibrated - 0.5) > 0
    assert abs(calibrated - 0.5) < abs(raw_prob - 0.5)


def test_predict_from_raw_inputs_shrinkage_defaults_to_config_for_old_snapshots():
    # Un snapshot congelado ANTES de esta clave debe recalcular con el valor
    # vigente de config (mismo criterio de compatibilidad que
    # negbin_dispersion/park_factor_weight).
    from config import SKELLAM_SHRINKAGE_ALPHA

    raw_old_snapshot = _base_raw_inputs()
    assert "skellam_shrinkage_alpha" not in raw_old_snapshot

    old_result = predict_from_raw_inputs(raw_old_snapshot)
    explicit_result = predict_from_raw_inputs(
        _base_raw_inputs(skellam_shrinkage_alpha=SKELLAM_SHRINKAGE_ALPHA)
    )
    assert old_result == explicit_result


def test_predict_from_raw_inputs_shrinkage_does_not_touch_run_line_totals_or_mu():
    # El barrido de calibración se ajustó SOLO contra moneyline -- los mu
    # proyectados, run line y total justo deben ser idénticos con cualquier
    # alpha (se derivan de los mu crudos, no de la probabilidad de victoria).
    raw_result = predict_from_raw_inputs(_base_raw_inputs(skellam_shrinkage_alpha=1.0))
    calibrated_result = predict_from_raw_inputs(_base_raw_inputs(skellam_shrinkage_alpha=0.5))

    for key in ("away_proj_runs", "home_proj_runs", "home_covers_rl_prob",
                "away_covers_rl_prob", "fair_total_runs"):
        assert raw_result[key] == calibrated_result[key]


def test_decompose_from_raw_inputs_final_runs_match_predict_from_raw_inputs():
    # decompose_from_raw_inputs() reusa _resolve_prediction_inputs() --
    # nunca puede desincronizarse del away_proj_runs/home_proj_runs real
    # que guardó la predicción, solo agrega el desglose por componente.
    raw = _base_raw_inputs(park_factor=1.1, temp_f=95)
    prediction = predict_from_raw_inputs(raw)
    decomposition = decompose_from_raw_inputs(raw)

    assert decomposition["away"]["final_runs"] == prediction["away_proj_runs"]
    assert decomposition["home"]["final_runs"] == prediction["home_proj_runs"]


def test_decompose_from_raw_inputs_home_gets_local_contrib_away_does_not():
    raw = _base_raw_inputs()
    decomposition = decompose_from_raw_inputs(raw)

    assert decomposition["home"]["local_contrib"] > 0.0
    assert decomposition["away"]["local_contrib"] == 0.0


def test_decompose_from_raw_inputs_uses_shrunk_era_like_predict_from_raw_inputs():
    # El abridor visitante tiene poca muestra (20 IP) -- decompose debe
    # aplicarle el mismo shrinkage hacia el ERA de liga que predict_from_raw_inputs(),
    # no el ERA crudo, para que el pitcheo rival mostrado en el desglose sea
    # consistente con la proyección real que se guardó.
    raw = _base_raw_inputs(away_innings_pitched=20, home_innings_pitched=20)
    prediction = predict_from_raw_inputs(raw)
    decomposition = decompose_from_raw_inputs(raw)

    assert decomposition["away"]["final_runs"] == prediction["away_proj_runs"]
    assert decomposition["home"]["final_runs"] == prediction["home_proj_runs"]
