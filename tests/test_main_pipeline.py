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
    # real de este fixture (~0.635/0.365 -- PICK_PROBABILITY_SOURCE default
    # desde model/picks.py, moneyline ya no se genera desde el heurístico)
    # -> ni home ni away superan los umbrales de edge/EV.
    monkeypatch.setattr(main, "MARKET_ODDS", {999999: {"away": -174, "home": 174}})

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
