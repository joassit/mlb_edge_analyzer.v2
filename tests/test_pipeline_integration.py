"""
Test de integración con 2 juegos asimétricos simultáneos -- el escenario
que habría atrapado la regresión original (probabilidades hardcodeadas a
0.5) si hubiera existido antes del refactor. Complementa a
tests/test_main_pipeline.py (que cubre 1 juego con más profundidad de
casos) verificando que analyze_today() sostiene sus invariantes con
múltiples juegos en la misma corrida.
"""

import main


def _fake_get_schedule_two_games(target_date=None):
    return [
        {
            "game_pk": 111,
            "away_team": "Strong Away", "home_team": "Weak Home",
            "away_team_id": 1, "home_team_id": 2,
            "away_pitcher_id": 10, "away_pitcher_name": "Ace",
            "home_pitcher_id": 20, "home_pitcher_name": "Scrub",
            "game_time": "2026-07-05T23:05:00Z",
            "status": "Scheduled", "abstract_state": "Preview",
        },
        {
            "game_pk": 222,
            "away_team": "Weak Away", "home_team": "Strong Home",
            "away_team_id": 3, "home_team_id": 4,
            "away_pitcher_id": 30, "away_pitcher_name": "Scrub2",
            "home_pitcher_id": 40, "home_pitcher_name": "Ace2",
            "game_time": "2026-07-06T00:10:00Z",
            "status": "Scheduled", "abstract_state": "Preview",
        },
    ]


def _patch_two_game_pipeline(monkeypatch):
    era_ip_by_pitcher = {
        10: (2.50, 120.0), 20: (5.20, 80.0),   # juego 1: away claramente mejor
        30: (5.00, 80.0), 40: (2.60, 120.0),   # juego 2: home claramente mejor
    }
    ops_by_team = {1: 0.820, 2: 0.700, 3: 0.700, 4: 0.820}

    monkeypatch.setattr(main, "get_schedule", _fake_get_schedule_two_games)
    monkeypatch.setattr(main, "get_pitcher_era_ip", lambda pid, season=None: era_ip_by_pitcher[pid])
    monkeypatch.setattr(main, "get_team_ops", lambda tid, season=None: ops_by_team[tid])
    monkeypatch.setattr(main, "get_league_ops", lambda season=None: 0.750)
    monkeypatch.setattr(main, "get_league_era", lambda season=None: 4.30)
    monkeypatch.setattr(main, "get_league_runs_per_game", lambda season=None: 4.4)
    monkeypatch.setattr(main, "get_bullpen_era", lambda tid, season=None: 4.30)
    monkeypatch.setattr(main, "get_pitcher_command", lambda pid, season=None: {"k_pct": 0.25, "bb_pct": 0.08, "whip": 1.2})
    monkeypatch.setattr(main, "get_pitcher_rest", lambda pid, season=None: {"days_rest": 5, "last_outing_pitches": 90})
    monkeypatch.setattr(main, "get_park_info", lambda tid: {"name": "Test Park", "park_factor": 1.0, "lat": 40.0, "lon": -70.0})
    monkeypatch.setattr(main, "preload_weather", lambda games, park_lookup: {
        2: {"temp_f": 72, "wind_mph": 5, "wind_direction_deg": 180},
        4: {"temp_f": 72, "wind_mph": 5, "wind_direction_deg": 180},
    })
    monkeypatch.setattr(main, "fetch_moneyline_odds", lambda: [])
    monkeypatch.setattr(main, "MARKET_ODDS", {})
    monkeypatch.setattr(main, "MARKET_SPREADS", {})
    monkeypatch.setattr(main, "MARKET_TOTALS", {})


def test_analyze_today_returns_two_rows_for_two_games(monkeypatch):
    _patch_two_game_pipeline(monkeypatch)
    results = main.analyze_today()
    assert len(results) == 2


def test_analyze_today_probabilities_sum_to_one_for_both_games(monkeypatch):
    _patch_two_game_pipeline(monkeypatch)
    results = main.analyze_today()
    for row in results:
        assert abs((row["home_model_prob"] + row["away_model_prob"]) - 1.0) < 1e-6


def test_analyze_today_no_probability_is_exactly_half(monkeypatch):
    _patch_two_game_pipeline(monkeypatch)
    results = main.analyze_today()
    for row in results:
        assert row["home_model_prob"] != 0.5
        assert row["away_model_prob"] != 0.5
        assert row["home_skellam_prob"] != 0.5
        assert row["home_covers_rl_prob"] != 0.5


def test_analyze_today_favors_the_stronger_team_in_both_games(monkeypatch):
    _patch_two_game_pipeline(monkeypatch)
    results = main.analyze_today()
    by_pk = {r["game_pk"]: r for r in results}

    # Juego 111: el visitante es claramente mejor.
    assert by_pk[111]["away_model_prob"] > 0.55
    # Juego 222: el local es claramente mejor.
    assert by_pk[222]["home_model_prob"] > 0.55


def test_analyze_today_skellam_and_run_line_probabilities_are_valid_ranges(monkeypatch):
    _patch_two_game_pipeline(monkeypatch)
    results = main.analyze_today()
    for row in results:
        assert 0.0 < row["home_skellam_prob"] < 1.0
        assert 0.0 < row["home_covers_rl_prob"] < 1.0
        assert abs((row["home_skellam_prob"] + row["away_skellam_prob"]) - 1.0) < 1e-9
