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
    era_by_pitcher = {10: 3.00, 20: 4.50}   # away pitcher mucho mejor que el home
    ops_by_team = {1: 0.780, 2: 0.740}       # away ofensiva mejor que la liga, home peor

    monkeypatch.setattr(main, "get_schedule", _fake_get_schedule)
    monkeypatch.setattr(main, "get_pitcher_era", lambda pid, season=None: era_by_pitcher[pid])
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
