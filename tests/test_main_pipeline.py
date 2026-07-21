"""
Test de integración del orquestador (`main.analyze_today`).

Este es el nivel de prueba que faltaba antes de esta auditoría: los tests
unitarios de `model/*.py` pasaban aunque el pipeline real nunca llamara a
esas funciones (quedó hardcodeado en 0.5/0.5). Este test mockea toda la
capa de red y verifica que el pipeline efectivamente invoque el modelo real
y produzca probabilidades y edge coherentes — no placeholders.
"""

import main


def _fake_get_schedule(target_date=None):
    return [{
        "game_pk": 999999,
        "away_team": "Away Team",
        "home_team": "Home Team",
        "away_team_id": 1,
        "home_team_id": 2,
        "away_pitcher_id": 10,
        "away_pitcher_name": "Ace Away",
        "home_pitcher_id": 20,
        "home_pitcher_name": "Ace Home",
        "game_time": "2026-07-05T23:05:00Z",
        "status": "Scheduled",
        "abstract_state": "Preview",
    }]


def _patch_pipeline(monkeypatch):
    # (era, innings_pitched) -- IP alta a propósito para que el shrinkage
    # hacia el promedio de liga no borre la diferencia real entre pitchers.
    era_ip_by_pitcher = {10: (3.00, 100.0), 20: (4.50, 90.0)}   # away pitcher mucho mejor que el home
    ops_by_team = {1: 0.780, 2: 0.740}       # away ofensiva mejor que la liga, home peor

    monkeypatch.setattr(main, "get_schedule", _fake_get_schedule)
    monkeypatch.setattr(main, "get_pitcher_era_ip", lambda pid, season=None: era_ip_by_pitcher[pid])
    monkeypatch.setattr(main, "get_team_ops", lambda tid, season=None: ops_by_team[tid])
    monkeypatch.setattr(main, "get_league_ops", lambda season=None: 0.750)
    monkeypatch.setattr(main, "get_league_era", lambda season=None: 4.30)
    monkeypatch.setattr(main, "get_league_runs_per_game", lambda season=None: 4.4)
    monkeypatch.setattr(main, "get_bullpen_era", lambda tid, season=None: 4.30)
    monkeypatch.setattr(main, "get_pitcher_command", lambda pid, season=None: {"k_pct": 0.25, "bb_pct": 0.08, "whip": 1.2})
    monkeypatch.setattr(main, "get_pitcher_rest", lambda pid, season=None: {"days_rest": 5, "last_outing_pitches": 90})
    monkeypatch.setattr(main, "get_park_info", lambda tid: {"name": "Test Park", "park_factor": 1.0, "lat": 40.0, "lon": -70.0})
    monkeypatch.setattr(main, "preload_weather", lambda games, park_lookup: {2: {"temp_f": 72, "wind_mph": 5, "wind_direction_deg": 180}})


def test_analyze_today_computes_real_probabilities_not_hardcoded_defaults(monkeypatch):
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {})

    results = main.analyze_today()

    assert len(results) == 1
    row = results[0]

    # El bug que esta prueba existe para atrapar: probabilidades hardcodeadas a 0.5
    assert row["away_model_prob"] != 0.5
    assert row["home_model_prob"] != 0.5
    assert row["away_skellam_prob"] != 0.5
    assert row["home_skellam_prob"] != 0.5

    # Deben seguir siendo probabilidades válidas y complementarias
    assert abs((row["away_model_prob"] + row["home_model_prob"]) - 1.0) < 1e-9
    assert abs((row["away_skellam_prob"] + row["home_skellam_prob"]) - 1.0) < 1e-9

    # Con un abridor y una ofensiva claramente superiores, el visitante debe
    # ser favorito en ambos modelos independientes.
    assert row["away_model_prob"] > 0.5
    assert row["away_skellam_prob"] > 0.5

    # Sin cuotas cargadas para este game_pk, no debe inventarse un edge
    assert row["away_edge"] is None
    assert row["home_edge"] is None


def test_analyze_today_uses_live_league_era_and_runs_per_game_not_hardcoded_constants(monkeypatch):
    # A2: antes LEAGUE_AVG_ERA/LEAGUE_AVG_RUNS_PER_GAME (model/runs_projection.py)
    # eran constantes fijas hardcodeadas en raw_inputs, sin importar lo que
    # devolvieran get_league_era()/get_league_runs_per_game() -- si esta
    # prueba pasara con la versión vieja del código (antes de A2),
    # cambiar esos mocks no debería afectar la proyección en absoluto.
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {})
    monkeypatch.setattr(main, "get_league_era", lambda season=None: 4.30)
    monkeypatch.setattr(main, "get_league_runs_per_game", lambda season=None: 4.4)

    baseline_results = main.analyze_today()
    baseline_row = baseline_results[0]

    # Valores MUY distintos a las constantes hardcodeadas de
    # model/runs_projection.py (LEAGUE_AVG_ERA=4.30, LEAGUE_AVG_RUNS_PER_GAME=4.4).
    monkeypatch.setattr(main, "get_league_era", lambda season=None: 7.0)
    monkeypatch.setattr(main, "get_league_runs_per_game", lambda season=None: 9.0)

    live_results = main.analyze_today()
    live_row = live_results[0]

    assert live_row["away_proj_runs"] != baseline_row["away_proj_runs"]
    assert live_row["home_proj_runs"] != baseline_row["home_proj_runs"]

    # Los valores en vivo quedan congelados en el snapshot -- no la constante.
    snapshot = live_results[0]["_feature_snapshot"]
    assert snapshot["league_era"] == 7.0
    assert snapshot["league_avg_runs_per_game"] == 9.0


def test_analyze_today_computes_edge_when_market_odds_present(monkeypatch):
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {999999: {"away": -150, "home": 130}})

    results = main.analyze_today()
    row = results[0]

    assert row["away_market_prob"] is not None
    assert row["away_edge"] is not None
    assert row["home_edge"] is not None
    # edge = model_prob - implied_prob del mercado, con implied_prob(-150) = 150/250 = 0.6
    assert abs(row["away_market_prob"] - 0.6) < 1e-9
    assert abs(row["away_edge"] - (row["away_model_prob"] - 0.6)) < 1e-9


def test_analyze_today_prefers_live_odds_over_manual_market_odds(monkeypatch):
    _patch_pipeline(monkeypatch)
    # MARKET_ODDS manual queda cargado, pero debe ganar la cuota en vivo
    monkeypatch.setattr(main, "MARKET_ODDS", {999999: {"away": -999, "home": 999}})
    live_event = {
        "away_team": "Away Team", "home_team": "Home Team", "commence_time": None,
        "prices": [{"book": "fakebook", "away_price": -140, "home_price": 120, "last_update": None}],
    }
    monkeypatch.setattr(main, "fetch_moneyline_odds", lambda: [live_event])

    results = main.analyze_today()
    row = results[0]

    assert row["away_market_prob"] is not None
    assert abs(row["away_market_prob"] - main.implied_prob(-140)) < 1e-9
    assert row["away_market_no_vig_prob"] is not None
    assert abs((row["away_market_no_vig_prob"] + row["home_market_no_vig_prob"]) - 1.0) < 1e-9


# --- Auditabilidad de momios: el reporte necesita saber el momio crudo
# usado y de dónde salió (API en vivo/caché/manual), no solo la
# probabilidad ya derivada -- ver reports/generate_report.py. ---

def test_analyze_today_records_raw_odds_and_live_source_when_odds_api_used(monkeypatch):
    import datetime as dt
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {})
    live_event = {
        "away_team": "Away Team", "home_team": "Home Team", "commence_time": None,
        "prices": [{"book": "fakebook", "away_price": -140, "home_price": 120, "last_update": None}],
    }
    monkeypatch.setattr(main, "fetch_moneyline_odds", lambda: [live_event])
    fetched_at = dt.datetime(2026, 7, 7, 12, 0, tzinfo=dt.timezone.utc).timestamp()
    monkeypatch.setattr(main, "get_last_fetch_meta", lambda: {"source": "api_live", "fetched_at": fetched_at})

    results = main.analyze_today()
    row = results[0]

    assert row["away_odds"] == -140
    assert row["home_odds"] == 120
    assert row["market_price_source"] == "api_live"
    assert row["market_captured_at"] == dt.datetime(2026, 7, 7, 12, 0)


def test_analyze_today_marks_source_as_manual_when_no_live_odds_match(monkeypatch):
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {999999: {"away": -150, "home": 130}})
    monkeypatch.setattr(main, "fetch_moneyline_odds", lambda: [])
    monkeypatch.setattr(main, "get_last_fetch_meta", lambda: {"source": "none", "fetched_at": None})

    results = main.analyze_today()
    row = results[0]

    assert row["away_odds"] == -150
    assert row["home_odds"] == 130
    assert row["market_price_source"] == "manual"
    assert row["market_captured_at"] is None  # MARKET_ODDS no trae timestamp de captura


def test_analyze_today_market_odds_metadata_is_none_without_any_price(monkeypatch):
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {})
    monkeypatch.setattr(main, "fetch_moneyline_odds", lambda: [])
    monkeypatch.setattr(main, "get_last_fetch_meta", lambda: {"source": "none", "fetched_at": None})

    results = main.analyze_today()
    row = results[0]

    assert row["away_odds"] is None
    assert row["home_odds"] is None
    assert row["market_price_source"] is None
    assert row["market_captured_at"] is None


def test_analyze_today_freezes_power_devig_reference_in_snapshot_without_using_it_for_edge(monkeypatch):
    # M4: market_no_vig_power es una referencia secundaria congelada en el
    # snapshot -- no debe alimentar away_market_prob/away_edge (esos
    # siguen viniendo del consenso proporcional, no_vig_probs).
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {})
    live_event = {
        "away_team": "Away Team", "home_team": "Home Team", "commence_time": None,
        "prices": [{"book": "fakebook", "away_price": -250, "home_price": 210, "last_update": None}],
    }
    monkeypatch.setattr(main, "fetch_moneyline_odds", lambda: [live_event])

    results = main.analyze_today()
    row = results[0]
    snapshot = row["_feature_snapshot"]

    assert snapshot["market_no_vig_power"] is not None
    away_power, home_power = snapshot["market_no_vig_power"]
    assert abs((away_power + home_power) - 1.0) < 1e-9

    # Distinto del consenso proporcional que sí decide el edge -- si
    # fueran iguales, esta prueba no protegería nada (podría estar
    # llamando a la función equivocada por coincidencia).
    assert abs(away_power - row["away_market_no_vig_prob"]) > 1e-6


def test_analyze_today_disambiguates_doubleheader_odds_by_game_time(monkeypatch):
    """
    Dos eventos de cuotas con los mismos equipos (doubleheader) -- sin
    pasar game_time a match_odds_to_game(), siempre ganaría el primero
    (el juego 1), asignándole sus cuotas también al juego 2. main.py debe
    pasar g["game_time"] para que se desambigüe por commence_time.
    """
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {})
    game1_event = {
        "away_team": "Away Team", "home_team": "Home Team", "commence_time": "2026-07-05T17:05:00Z",
        "prices": [{"book": "fakebook", "away_price": -300, "home_price": 250, "last_update": None}],
    }
    game2_event = {
        "away_team": "Away Team", "home_team": "Home Team", "commence_time": "2026-07-05T23:05:00Z",
        "prices": [{"book": "fakebook", "away_price": -140, "home_price": 120, "last_update": None}],
    }
    monkeypatch.setattr(main, "fetch_moneyline_odds", lambda: [game1_event, game2_event])

    results = main.analyze_today()
    row = results[0]

    # _fake_get_schedule() da game_time="2026-07-05T23:05:00Z" -> debe
    # matchear game2_event (-140/120), NO game1_event (-300/250).
    assert abs(row["away_market_prob"] - main.implied_prob(-140)) < 1e-9


def test_analyze_today_builds_feature_snapshot_with_raw_inputs(monkeypatch):
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {})

    results = main.analyze_today()
    snapshot = results[0]["_feature_snapshot"]

    assert snapshot["away_era"] == 3.00
    assert snapshot["home_era"] == 4.50
    assert snapshot["away_ops"] == 0.780
    assert snapshot["park_factor"] == 1.0
    assert snapshot["market_price"] is None


def test_analyze_today_reports_market_favorite_and_edge_vs_favorite(monkeypatch):
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {999999: {"away": -150, "home": 130}})

    results = main.analyze_today()
    row = results[0]

    # Con una sola cuota manual (no hay no-vig), el favorito se calcula
    # sobre la probabilidad implícita con vig -- away_market_prob=0.6 > home.
    assert row["market_favorite_team"] == "Away Team"
    assert row["market_favorite_side"] == "away"
    assert abs(row["market_favorite_prob"] - 0.6) < 1e-9
    assert row["model_edge_vs_market_favorite"] == row["away_edge"]


def test_analyze_today_favorite_uses_no_vig_consensus_when_available(monkeypatch):
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {})
    live_event = {
        "away_team": "Away Team", "home_team": "Home Team", "commence_time": None,
        "prices": [{"book": "fakebook", "away_price": -140, "home_price": 120, "last_update": None}],
    }
    monkeypatch.setattr(main, "fetch_moneyline_odds", lambda: [live_event])

    results = main.analyze_today()
    row = results[0]

    assert row["market_favorite_team"] == "Away Team"
    assert row["market_favorite_side"] == "away"
    assert abs(row["market_favorite_prob"] - row["away_market_no_vig_prob"]) < 1e-9


def test_analyze_today_flags_review_when_edge_and_models_agree(monkeypatch):
    _patch_pipeline(monkeypatch)
    # Cuota generosa para el visitante -> genera edge grande a su favor,
    # y el visitante ya es favorito en ambos modelos (heurístico y Skellam).
    monkeypatch.setattr(main, "MARKET_ODDS", {999999: {"away": 200, "home": -250}})

    results = main.analyze_today()
    row = results[0]

    assert row["flag_review"] is True


def test_analyze_today_does_not_flag_review_without_market_odds(monkeypatch):
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {})

    results = main.analyze_today()
    row = results[0]

    assert row["flag_review"] is False


def test_analyze_today_high_confidence_follows_heuristic_threshold(monkeypatch):
    # La señal debe reflejar EXACTAMENTE si la confianza del heurístico en
    # su favorito (max(away_model_prob, home_model_prob)) alcanza
    # HIGH_CONFIDENCE_THRESHOLD -- se verifica la coherencia interna con las
    # probabilidades reales del propio row, sin acoplarse a un valor
    # numérico específico del fixture.
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {})

    results = main.analyze_today()
    row = results[0]

    expected = max(row["away_model_prob"], row["home_model_prob"]) >= main.HIGH_CONFIDENCE_THRESHOLD
    assert row["high_confidence"] is expected


def test_analyze_today_high_confidence_true_when_threshold_forced_low(monkeypatch):
    # Con el umbral forzado a 0.50, cualquier juego (todo favorito tiene
    # confianza >= 0.5 por definición) debe quedar marcado -- prueba que el
    # flag realmente lee el umbral y no un booleano fijo.
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {})
    monkeypatch.setattr(main, "HIGH_CONFIDENCE_THRESHOLD", 0.50)

    results = main.analyze_today()
    assert results[0]["high_confidence"] is True


def test_analyze_today_high_confidence_false_when_threshold_forced_high(monkeypatch):
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {})
    monkeypatch.setattr(main, "HIGH_CONFIDENCE_THRESHOLD", 0.99)

    results = main.analyze_today()
    assert results[0]["high_confidence"] is False


def _clear_manual_markets(monkeypatch):
    monkeypatch.setattr(main, "MARKET_ODDS", {})
    monkeypatch.setattr(main, "MARKET_SPREADS", {})
    monkeypatch.setattr(main, "MARKET_TOTALS", {})
    monkeypatch.setattr(main, "fetch_moneyline_odds", lambda: [])


def test_analyze_today_generates_up_to_three_picks_with_all_markets_loaded(monkeypatch):
    _patch_pipeline(monkeypatch)
    _clear_manual_markets(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {999999: {"away": 200, "home": -250}})
    monkeypatch.setattr(main, "MARKET_SPREADS", {999999: {"line": 1.5, "home": 180, "away": -220}})
    monkeypatch.setattr(main, "MARKET_TOTALS", {999999: {"line": 8.5, "over": 150, "under": -180}})

    results = main.analyze_today()
    picks = results[0]["_picks"]

    markets = {p["market"] for p in picks}
    assert markets.issubset({"moneyline", "run_line", "totals"})
    assert len(picks) <= 3
    assert len(picks) >= 1


def test_analyze_today_no_moneyline_required_only_totals_loaded(monkeypatch):
    _patch_pipeline(monkeypatch)
    _clear_manual_markets(monkeypatch)
    monkeypatch.setattr(main, "MARKET_TOTALS", {999999: {"line": 8.5, "over": 150, "under": -180}})

    results = main.analyze_today()
    picks = results[0]["_picks"]

    assert len(picks) == 1
    assert picks[0]["market"] == "totals"


def test_analyze_today_forces_a_pick_when_no_market_has_edge(monkeypatch):
    _patch_pipeline(monkeypatch)
    _clear_manual_markets(monkeypatch)
    # Cuota casi exactamente igual a la que implica la probabilidad Skellam
    # CALIBRADA de este fixture (~0.635 cruda -> ~0.5675 tras la contracción
    # alpha=0.5 de SKELLAM_SHRINKAGE_ALPHA; PICK_PROBABILITY_SOURCE default
    # desde model/picks.py) -> ni home ni away superan los umbrales de
    # edge/EV. Antes de la calibración esta cuota era -174/+174 (la
    # probabilidad cruda); si SKELLAM_SHRINKAGE_ALPHA cambia, este par de
    # momios tiene que reajustarse con él.
    monkeypatch.setattr(main, "MARKET_ODDS", {999999: {"away": -131, "home": 131}})

    results = main.analyze_today()
    picks = results[0]["_picks"]

    assert len(picks) == 1
    assert picks[0]["forced"] is True


def test_analyze_today_generates_no_picks_without_any_market_data(monkeypatch):
    _patch_pipeline(monkeypatch)
    _clear_manual_markets(monkeypatch)

    results = main.analyze_today()

    assert results[0]["_picks"] == []


def test_analyze_today_feature_snapshot_captures_manual_run_line_and_totals(monkeypatch):
    _patch_pipeline(monkeypatch)
    _clear_manual_markets(monkeypatch)
    monkeypatch.setattr(main, "MARKET_SPREADS", {999999: {"line": 1.5, "home": 180, "away": -220}})
    monkeypatch.setattr(main, "MARKET_TOTALS", {999999: {"line": 8.5, "over": 150, "under": -180}})

    results = main.analyze_today()
    snapshot = results[0]["_feature_snapshot"]

    assert snapshot["market_run_line"] == {"line": 1.5, "home": 180, "away": -220}
    assert snapshot["market_totals"] == {"line": 8.5, "over": 150, "under": -180}


def test_analyze_today_uses_official_game_date_from_schedule(monkeypatch):
    _patch_pipeline(monkeypatch)
    _clear_manual_markets(monkeypatch)

    def fake_schedule_with_official_date(target_date=None):
        games = _fake_get_schedule(target_date)
        games[0]["game_date_official"] = "2026-07-04"
        return games

    monkeypatch.setattr(main, "get_schedule", fake_schedule_with_official_date)

    results = main.analyze_today()

    # game_date debe ser la fecha oficial ET del juego (de la API), no la
    # fecha local de la máquina que corre el pipeline.
    assert results[0]["game_date"] == "2026-07-04"


def test_analyze_today_falls_back_to_today_without_official_game_date(monkeypatch):
    from datetime import date as _date

    _patch_pipeline(monkeypatch)
    _clear_manual_markets(monkeypatch)
    # _fake_get_schedule() no incluye game_date_official -- debe caer a hoy.

    results = main.analyze_today()

    assert results[0]["game_date"] == _date.today().strftime("%Y-%m-%d")


def _fake_get_schedule_two_games(target_date=None):
    return [
        {
            "game_pk": 111, "away_team": "A", "home_team": "B",
            "away_team_id": 1, "home_team_id": 2,
            "away_pitcher_id": 10, "away_pitcher_name": "P1",
            "home_pitcher_id": 20, "home_pitcher_name": "P2",
            "game_time": "2026-07-05T23:05:00Z", "status": "Scheduled", "abstract_state": "Preview",
        },
        {
            "game_pk": 222, "away_team": "C", "home_team": "D",
            "away_team_id": 3, "home_team_id": 4,
            "away_pitcher_id": 30, "away_pitcher_name": "P3",
            "home_pitcher_id": 40, "home_pitcher_name": "P4",
            "game_time": "2026-07-06T00:10:00Z", "status": "Scheduled", "abstract_state": "Preview",
        },
    ]


def test_analyze_today_isolates_an_unexpected_error_in_one_game(monkeypatch):
    """Un juego con un error inesperado (ej. un cambio de esquema que
    provoque un KeyError/AttributeError no anticipado) no debe tumbar el
    resto del día -- se descarta solo ese juego, se cuenta como error, y
    los demás juegos se procesan con normalidad."""
    era_ip_by_pitcher = {10: (3.00, 100.0), 20: (4.50, 90.0), 30: (3.50, 100.0), 40: (4.00, 90.0)}
    ops_by_team = {1: 0.780, 2: 0.740, 3: 0.760, 4: 0.750}

    def _bullpen_era_raises_for_team_2(team_id, season=None):
        if team_id == 2:
            raise KeyError("cambio de esquema inesperado en el roster")
        return 4.30

    monkeypatch.setattr(main, "get_schedule", _fake_get_schedule_two_games)
    monkeypatch.setattr(main, "get_pitcher_era_ip", lambda pid, season=None: era_ip_by_pitcher[pid])
    monkeypatch.setattr(main, "get_team_ops", lambda tid, season=None: ops_by_team[tid])
    monkeypatch.setattr(main, "get_league_ops", lambda season=None: 0.750)
    monkeypatch.setattr(main, "get_league_era", lambda season=None: 4.30)
    monkeypatch.setattr(main, "get_league_runs_per_game", lambda season=None: 4.4)
    monkeypatch.setattr(main, "get_bullpen_era", _bullpen_era_raises_for_team_2)
    monkeypatch.setattr(main, "get_pitcher_command", lambda pid, season=None: {"k_pct": 0.25, "bb_pct": 0.08, "whip": 1.2})
    monkeypatch.setattr(main, "get_pitcher_rest", lambda pid, season=None: {"days_rest": 5, "last_outing_pitches": 90})
    monkeypatch.setattr(main, "get_park_info", lambda tid: {"name": "Test Park", "park_factor": 1.0, "lat": 40.0, "lon": -70.0})
    monkeypatch.setattr(main, "preload_weather", lambda games, park_lookup: {})
    monkeypatch.setattr(main, "fetch_moneyline_odds", lambda: [])
    monkeypatch.setattr(main, "MARKET_ODDS", {})
    monkeypatch.setattr(main, "MARKET_SPREADS", {})
    monkeypatch.setattr(main, "MARKET_TOTALS", {})

    results = main.analyze_today()

    # El juego 111 (home_team_id=2) explota -- se descarta. El 222 sigue.
    assert len(results) == 1
    assert results[0]["game_pk"] == 222

    stats = main.analyze_today.last_run_stats
    assert stats["total_games"] == 2
    assert stats["processed"] == 1
    assert stats["errors"] == 1
    assert stats["discarded"] == 0


def test_analyze_today_last_run_stats_counts_discarded_games(monkeypatch):
    def _fake_schedule_with_tbd(target_date=None):
        games = _fake_get_schedule_two_games(target_date)
        games[0]["away_pitcher_id"] = None  # TBD -> descartado, no error
        return games

    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "get_schedule", _fake_schedule_with_tbd)
    monkeypatch.setattr(main, "get_pitcher_era_ip", lambda pid, season=None: {30: (3.5, 100.0), 40: (4.0, 90.0)}[pid])
    monkeypatch.setattr(main, "get_team_ops", lambda tid, season=None: {3: 0.760, 4: 0.750}[tid])
    _clear_manual_markets(monkeypatch)

    results = main.analyze_today()

    assert len(results) == 1
    stats = main.analyze_today.last_run_stats
    assert stats["total_games"] == 2
    assert stats["processed"] == 1
    assert stats["discarded"] == 1
    assert stats["errors"] == 0


# --- _mu_family_agrees_internally / _heuristic_agrees_with_mu_family ---
# Skellam y NB2 comparten el mismo mu proyectado -- son un solo voto real
# ("familia mu"), no dos independientes. Estas dos funciones puras son las
# que deciden qué cuenta como "los modelos coinciden" en flag_review.

def test_mu_family_agrees_internally_true_when_both_favor_same_side():
    assert main._mu_family_agrees_internally(away_skellam_prob=0.65, away_negbin_prob=0.60) is True


def test_mu_family_agrees_internally_false_when_they_disagree():
    # Skellam favorece al visitante (>0.5), NB2 favorece al local (<0.5)
    # -- el caso raro que de verdad importa detectar.
    assert main._mu_family_agrees_internally(away_skellam_prob=0.51, away_negbin_prob=0.49) is False


def test_mu_family_agrees_internally_exact_tie_at_half_counts_as_agreement():
    # Ni Skellam ni NB2 favorecen a nadie (exactamente 0.5 -- "away_prob > 0.5"
    # es False para ambos) -- False == False cuenta como acuerdo, mismo
    # criterio que ya usaba el chequeo original de dos modelos.
    assert main._mu_family_agrees_internally(away_skellam_prob=0.5, away_negbin_prob=0.5) is True


def test_heuristic_agrees_with_mu_family_true_when_same_side():
    assert main._heuristic_agrees_with_mu_family(away_model_prob=0.55, away_skellam_prob=0.60) is True


def test_heuristic_agrees_with_mu_family_false_when_different_sides():
    assert main._heuristic_agrees_with_mu_family(away_model_prob=0.55, away_skellam_prob=0.45) is False


def test_heuristic_agrees_with_mu_family_exact_tie_at_half_counts_as_agreement():
    assert main._heuristic_agrees_with_mu_family(away_model_prob=0.5, away_skellam_prob=0.5) is True


# --- Visibilidad de descartes: _discard_reason_phrase / _build_discard_message ---
# Antes de esta auditoría, un juego descartado por estado (in progress,
# pospuesto, suspendido...) se loggeaba a nivel DEBUG -- invisible en la
# consola de GitHub Actions (el StreamHandler de logging_config.py solo
# muestra WARNING+). Esto causó confusión real: un descarte de Phillies @
# Royals no pudo explicarse en el reporte. Estas pruebas cubren las
# funciones puras que arman el mensaje detallado.

def test_discard_reason_phrase_distinguishes_in_progress():
    assert main._discard_reason_phrase("In Progress") == "ya estaba en curso"


def test_discard_reason_phrase_distinguishes_postponed_from_in_progress():
    assert main._discard_reason_phrase("Postponed") == "pospuesto"
    assert main._discard_reason_phrase("Postponed") != main._discard_reason_phrase("In Progress")


def test_discard_reason_phrase_distinguishes_suspended():
    assert main._discard_reason_phrase("Suspended: Rain") == "suspendido"


def test_discard_reason_phrase_distinguishes_delayed():
    assert main._discard_reason_phrase("Delayed Start") == "con inicio retrasado"


def test_discard_reason_phrase_falls_back_for_unknown_state():
    assert main._discard_reason_phrase("Something New") == "estado inusual (Something New)"


_GAME_IN_PROGRESS = {
    "away_team": "Philadelphia Phillies", "home_team": "Kansas City Royals",
    "status": "In Progress", "game_time": "2026-07-06T15:03:00Z", "game_number": 1,
}


def test_build_discard_message_names_teams_and_real_state_for_in_progress():
    msg = main._build_discard_message(_GAME_IN_PROGRESS, other_games_same_matchup=[])
    assert "Philadelphia Phillies @ Kansas City Royals" in msg
    assert "ya estaba en curso" in msg
    assert "estado: In Progress" in msg
    assert "15:03 UTC" in msg


def test_build_discard_message_postponed_differs_from_in_progress():
    postponed_game = {**_GAME_IN_PROGRESS, "status": "Postponed"}
    msg_in_progress = main._build_discard_message(_GAME_IN_PROGRESS, other_games_same_matchup=[])
    msg_postponed = main._build_discard_message(postponed_game, other_games_same_matchup=[])
    assert msg_in_progress != msg_postponed
    assert "pospuesto" in msg_postponed
    assert "ya estaba en curso" not in msg_postponed


def test_build_discard_message_doubleheader_identifies_both_games():
    # Juego 1 (Phillies @ Royals) en curso; juego 2 del mismo matchup sigue
    # en Preview y sí se procesó -- exactamente el caso que confundió el
    # reporte real del 2026-07-06.
    game_1 = _GAME_IN_PROGRESS
    game_2 = {
        "away_team": "Philadelphia Phillies", "home_team": "Kansas City Royals",
        "status": "Scheduled", "abstract_state": "Preview",
        "game_time": "2026-07-06T20:05:00Z", "game_number": 2,
    }

    msg = main._build_discard_message(game_1, other_games_same_matchup=[game_2])

    assert "juego 1 de doble cartelera" in msg
    assert "ya estaba en curso" in msg
    assert "juego 2 sigue en Preview y sí se procesó" in msg


def test_build_discard_message_doubleheader_both_games_not_processed():
    game_1 = _GAME_IN_PROGRESS
    game_2_postponed = {
        "away_team": "Philadelphia Phillies", "home_team": "Kansas City Royals",
        "status": "Postponed", "abstract_state": "Postponed",
        "game_time": "2026-07-06T20:05:00Z", "game_number": 2,
    }

    msg = main._build_discard_message(game_1, other_games_same_matchup=[game_2_postponed])

    assert "juego 1 de doble cartelera" in msg
    assert "juego 2 tampoco se procesó" in msg
    assert "pospuesto" in msg


def test_analyze_today_populates_discarded_games_with_detailed_message(monkeypatch):
    def _fake_schedule_in_progress(target_date=None):
        games = _fake_get_schedule_two_games(target_date)
        games[0]["abstract_state"] = "Live"
        games[0]["status"] = "In Progress"
        return games

    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "get_schedule", _fake_schedule_in_progress)
    monkeypatch.setattr(main, "get_pitcher_era_ip", lambda pid, season=None: {30: (3.5, 100.0), 40: (4.0, 90.0)}[pid])
    monkeypatch.setattr(main, "get_team_ops", lambda tid, season=None: {3: 0.760, 4: 0.750}[tid])
    _clear_manual_markets(monkeypatch)

    main.analyze_today()

    stats = main.analyze_today.last_run_stats
    assert stats["discarded"] == 1
    assert len(stats["discarded_games"]) == 1
    message = stats["discarded_games"][0]["message"]
    assert "A @ B" in message
    assert "ya estaba en curso" in message
    assert "estado: In Progress" in message


# --- Exclusión de juegos ya "Final" (contaminación de métricas) ---
# get_pitcher_era_ip()/get_team_ops() piden stats de TEMPORADA acumuladas en
# el momento de la corrida, sin corte "as of date" -- si un juego ya
# terminó, esas stats ya incluyen su propio resultado. Antes, "Final" se
# aceptaba junto con "Preview" y se le generaba una "predicción" retroactiva
# contaminada. Ahora se descarta igual que cualquier otro estado no-Preview.

def test_discard_reason_phrase_for_final_state():
    assert main._discard_reason_phrase("Final") == "ya terminó (Final) antes de correr el pipeline"


def test_build_discard_message_for_final_game():
    final_game = {
        "away_team": "Philadelphia Phillies", "home_team": "Kansas City Royals",
        "status": "Final", "game_time": "2026-07-06T15:03:00Z", "game_number": 1,
    }
    msg = main._build_discard_message(final_game, other_games_same_matchup=[])
    assert "Philadelphia Phillies @ Kansas City Royals" in msg
    assert "ya terminó (Final) antes de correr el pipeline" in msg
    assert "estado: Final" in msg


def test_analyze_today_discards_final_games_instead_of_processing_them(monkeypatch):
    """El bug real: un juego que ya terminó (Final) al momento de correr el
    pipeline no debe generar una predicción -- generarla usaría stats de
    temporada que ya incluyen el resultado de ese mismo juego."""
    def _fake_schedule_with_final_game(target_date=None):
        games = _fake_get_schedule_two_games(target_date)
        games[0]["abstract_state"] = "Final"
        games[0]["status"] = "Final"
        return games

    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(main, "get_schedule", _fake_schedule_with_final_game)
    monkeypatch.setattr(main, "get_pitcher_era_ip", lambda pid, season=None: {30: (3.5, 100.0), 40: (4.0, 90.0)}[pid])
    monkeypatch.setattr(main, "get_team_ops", lambda tid, season=None: {3: 0.760, 4: 0.750}[tid])
    _clear_manual_markets(monkeypatch)

    results = main.analyze_today()

    # Solo el juego C@D (Preview) se procesa -- A@B (Final) se descarta.
    assert len(results) == 1
    assert results[0]["game_pk"] == 222

    stats = main.analyze_today.last_run_stats
    assert stats["discarded"] == 1
    message = stats["discarded_games"][0]["message"]
    assert "ya terminó (Final) antes de correr el pipeline" in message


def test_build_discard_message_doubleheader_sibling_final_also_not_processed():
    # Juego 1 pospuesto; juego 2 del mismo matchup ya es Final -- desde el
    # fix, Final tampoco cuenta como "procesado", así que el mensaje debe
    # decir "tampoco se procesó", no "sí se procesó".
    game_1_postponed = {
        "away_team": "Philadelphia Phillies", "home_team": "Kansas City Royals",
        "status": "Postponed", "game_time": "2026-07-06T15:03:00Z", "game_number": 1,
    }
    game_2_final = {
        "away_team": "Philadelphia Phillies", "home_team": "Kansas City Royals",
        "status": "Final", "abstract_state": "Final",
        "game_time": "2026-07-06T20:05:00Z", "game_number": 2,
    }

    msg = main._build_discard_message(game_1_postponed, other_games_same_matchup=[game_2_final])

    assert "juego 1 de doble cartelera" in msg
    assert "pospuesto" in msg
    assert "juego 2 tampoco se procesó" in msg
    assert "ya terminó (Final) antes de correr el pipeline" in msg
    assert "sí se procesó" not in msg


# --- _sqlite_persistence_risk_warning: visibilidad del riesgo de persistencia ---
# mlb_edge.db sobre SQLite + actions/cache puede perderse en silencio
# (evicción a los 7+ días, o límite de 10GB del repo) -- el aviso no
# resuelve el riesgo (eso requiere un Postgres externo real, algo que solo
# el dueño del proyecto puede provisionar), pero lo hace visible en cada
# corrida en vez de quedar como un comentario que nadie lee.

def test_sqlite_persistence_risk_warning_fires_for_sqlite_url():
    warning = main._sqlite_persistence_risk_warning("sqlite:///mlb_edge.db")
    assert warning is not None
    assert "SQLite" in warning
    assert "DATABASE_URL" in warning


def test_sqlite_persistence_risk_warning_silent_for_external_database_url():
    warning = main._sqlite_persistence_risk_warning("postgresql://user:pass@host:5432/db")
    assert warning is None


# --- _calibration_phase_note: heurístico sin calibrar con muestra chica ---
# Con menos de config.MIN_LIQUIDATED_PICKS_FOR_CALIBRATION picks
# liquidados con cuota real, un "edge" del heurístico probablemente es
# ruido, no señal de mercado real.

def test_calibration_phase_note_fires_below_threshold():
    note = main._calibration_phase_note(n_liquidated_picks=34, min_liquidated_picks=200)
    assert note is not None
    assert "34/200" in note
    assert "🧪" in note


def test_calibration_phase_note_silent_at_or_above_threshold():
    assert main._calibration_phase_note(n_liquidated_picks=200, min_liquidated_picks=200) is None
    assert main._calibration_phase_note(n_liquidated_picks=500, min_liquidated_picks=200) is None
