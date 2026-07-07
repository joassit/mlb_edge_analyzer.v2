"""
V8 — analyze_today() debe dejar rastro en el log de por qué omite un
juego, en vez del `continue` silencioso original: sin esto, un juego que
no aparece en el reporte del día no se puede diagnosticar sin depurar el
código a mano.
"""

import logging

import main


def _fake_schedule_with_tbd_pitcher(target_date=None):
    return [{
        "game_pk": 888888,
        "away_team": "TBD Away", "home_team": "TBD Home",
        "away_team_id": 1, "home_team_id": 2,
        "away_pitcher_id": None, "away_pitcher_name": None,
        "home_pitcher_id": 20, "home_pitcher_name": "Ace",
        "game_time": "2026-07-05T23:05:00Z",
        "status": "Scheduled", "abstract_state": "Preview",
    }]


def _fake_schedule_with_confirmed_pitchers(target_date=None):
    return [{
        "game_pk": 888889,
        "away_team": "A", "home_team": "B",
        "away_team_id": 1, "home_team_id": 2,
        "away_pitcher_id": 10, "away_pitcher_name": "P1",
        "home_pitcher_id": 20, "home_pitcher_name": "P2",
        "game_time": "2026-07-05T23:05:00Z",
        "status": "Scheduled", "abstract_state": "Preview",
    }]


def test_analyze_today_logs_warning_when_probable_pitcher_missing(monkeypatch, caplog):
    monkeypatch.setattr(main, "get_schedule", _fake_schedule_with_tbd_pitcher)
    monkeypatch.setattr(main, "get_league_ops", lambda season=None: 0.750)
    monkeypatch.setattr(main, "preload_weather", lambda games, park_lookup: {})
    monkeypatch.setattr(main, "fetch_moneyline_odds", lambda: [])

    with caplog.at_level(logging.WARNING, logger="mlb_edge_analyzer"):
        results = main.analyze_today()

    assert results == []
    assert any("abridor probable" in r.message for r in caplog.records)


def test_analyze_today_logs_warning_when_stats_are_missing(monkeypatch, caplog):
    monkeypatch.setattr(main, "get_schedule", _fake_schedule_with_confirmed_pitchers)
    monkeypatch.setattr(main, "get_pitcher_era_ip", lambda pid, season=None: None)
    monkeypatch.setattr(main, "get_team_ops", lambda tid, season=None: None)
    monkeypatch.setattr(main, "get_league_ops", lambda season=None: 0.750)
    monkeypatch.setattr(main, "preload_weather", lambda games, park_lookup: {})
    monkeypatch.setattr(main, "fetch_moneyline_odds", lambda: [])

    with caplog.at_level(logging.WARNING, logger="mlb_edge_analyzer"):
        results = main.analyze_today()

    assert results == []
    assert any("falta" in r.message for r in caplog.records)


def _patch_full_pipeline_no_market_data(monkeypatch):
    monkeypatch.setattr(main, "get_schedule", _fake_schedule_with_confirmed_pitchers)
    monkeypatch.setattr(main, "get_pitcher_era_ip", lambda pid, season=None: (3.5, 100.0))
    monkeypatch.setattr(main, "get_team_ops", lambda tid, season=None: 0.750)
    monkeypatch.setattr(main, "get_league_ops", lambda season=None: 0.750)
    monkeypatch.setattr(main, "get_bullpen_era", lambda tid, season=None: 4.30)
    monkeypatch.setattr(main, "get_pitcher_command", lambda pid, season=None: {"k_pct": 0.25, "bb_pct": 0.08, "whip": 1.2})
    monkeypatch.setattr(main, "get_pitcher_rest", lambda pid, season=None: {"days_rest": 5, "last_outing_pitches": 90})
    monkeypatch.setattr(main, "get_park_info", lambda tid: {"name": "Test Park", "park_factor": 1.0, "lat": 40.0, "lon": -70.0})
    monkeypatch.setattr(main, "preload_weather", lambda games, park_lookup: {})
    monkeypatch.setattr(main, "fetch_moneyline_odds", lambda: [])
    monkeypatch.setattr(main, "MARKET_ODDS", {})
    monkeypatch.setattr(main, "MARKET_SPREADS", {})
    monkeypatch.setattr(main, "MARKET_TOTALS", {})


def test_analyze_today_logs_warning_when_no_market_data_available(monkeypatch, caplog):
    _patch_full_pipeline_no_market_data(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="mlb_edge_analyzer"):
        results = main.analyze_today()

    assert len(results) == 1
    assert any("sin datos de mercado" in r.message for r in caplog.records)


def test_analyze_today_warns_when_market_spreads_omits_favorite_side(monkeypatch, caplog):
    # C1: MARKET_SPREADS sin favorite_side explícito debe avisar (no fallar
    # en silencio) que se está asumiendo "home" como favorito.
    _patch_full_pipeline_no_market_data(monkeypatch)
    monkeypatch.setattr(main, "MARKET_SPREADS", {888889: {"line": 1.5, "home": -120, "away": 100}})

    with caplog.at_level(logging.WARNING, logger="mlb_edge_analyzer"):
        results = main.analyze_today()

    assert len(results) == 1
    assert any("favorite_side" in r.message for r in caplog.records)
    rl_pick = next(p for p in results[0]["_picks"] if p["market"] == "run_line")
    assert rl_pick["favorite_side"] == "home"


def test_analyze_today_does_not_warn_when_favorite_side_is_explicit(monkeypatch, caplog):
    _patch_full_pipeline_no_market_data(monkeypatch)
    monkeypatch.setattr(main, "MARKET_SPREADS",
                         {888889: {"line": 1.5, "favorite_side": "away", "home": -120, "away": 100}})

    with caplog.at_level(logging.WARNING, logger="mlb_edge_analyzer"):
        results = main.analyze_today()

    assert not any("favorite_side" in r.message for r in caplog.records)
    rl_pick = next(p for p in results[0]["_picks"] if p["market"] == "run_line")
    assert rl_pick["favorite_side"] == "away"


# --- C2: cuotas manuales inválidas se descartan (no generan edge fantasma) ---

def test_analyze_today_rejects_decimal_typo_in_manual_moneyline_odds(monkeypatch, caplog):
    # 1.91 en vez de -110 -- el typo real que describe el hallazgo C2.
    _patch_full_pipeline_no_market_data(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {888889: {"home": -110, "away": 1.91}})

    with caplog.at_level(logging.WARNING, logger="mlb_edge_analyzer"):
        results = main.analyze_today()

    assert len(results) == 1
    assert results[0]["away_market_prob"] is None  # todo el mercado se descarta, no solo el lado inválido
    assert results[0]["away_edge"] is None
    assert any("cuota manual inválida" in r.message and "1.91" in r.message for r in caplog.records)


def test_analyze_today_accepts_valid_manual_moneyline_odds(monkeypatch, caplog):
    _patch_full_pipeline_no_market_data(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {888889: {"home": -110, "away": 100}})

    with caplog.at_level(logging.WARNING, logger="mlb_edge_analyzer"):
        results = main.analyze_today()

    assert results[0]["away_market_prob"] is not None
    assert not any("cuota manual inválida" in r.message for r in caplog.records)


def test_analyze_today_rejects_invalid_run_line_and_pipeline_continues_with_other_markets(monkeypatch, caplog):
    # Un solo lado inválido en run_line no debe tumbar el juego -- el
    # mercado de moneyline (válido) debe seguir generando su pick normal.
    _patch_full_pipeline_no_market_data(monkeypatch)
    monkeypatch.setattr(main, "MARKET_ODDS", {888889: {"home": -110, "away": 100}})
    monkeypatch.setattr(main, "MARKET_SPREADS", {888889: {"line": 1.5, "home": -120, "away": 1.91}})

    with caplog.at_level(logging.WARNING, logger="mlb_edge_analyzer"):
        results = main.analyze_today()

    assert len(results) == 1
    picks = results[0]["_picks"]
    assert any(p["market"] == "moneyline" for p in picks)
    assert not any(p["market"] == "run_line" for p in picks)
    assert any("cuota manual inválida" in r.message and "run_line" in r.message for r in caplog.records)


def test_analyze_today_rejects_invalid_manual_totals_line(monkeypatch, caplog):
    _patch_full_pipeline_no_market_data(monkeypatch)
    monkeypatch.setattr(main, "MARKET_TOTALS", {888889: {"line": 8.5, "over": -110, "under": -110.5}})

    with caplog.at_level(logging.WARNING, logger="mlb_edge_analyzer"):
        results = main.analyze_today()

    picks = results[0]["_picks"]
    assert not any(p["market"] == "totals" for p in picks)
    assert any("cuota manual inválida" in r.message and "totals" in r.message for r in caplog.records)


def test_analyze_today_rejects_absurd_manual_totals_line_value(monkeypatch, caplog):
    _patch_full_pipeline_no_market_data(monkeypatch)
    monkeypatch.setattr(main, "MARKET_TOTALS", {888889: {"line": 100.0, "over": -110, "under": -110}})

    with caplog.at_level(logging.WARNING, logger="mlb_edge_analyzer"):
        results = main.analyze_today()

    picks = results[0]["_picks"]
    assert not any(p["market"] == "totals" for p in picks)
    assert any("línea manual inválida" in r.message for r in caplog.records)
