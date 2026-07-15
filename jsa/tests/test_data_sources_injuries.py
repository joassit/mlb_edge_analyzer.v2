"""`jsa/data_sources/injuries.py` -- version de PRODUCCION de lesiones IL
(duplicada a proposito de `jsa/historical/injuries.py`, ver docstring del
modulo). Mismo parseo/umbral/criterio point-in-time, HTTP mockeado sobre
`jsa.data_sources.injuries.session` -- nunca red real."""

from __future__ import annotations

from unittest.mock import Mock, patch

import requests

from jsa.data_sources.injuries import (
    ILEvent,
    build_injury_index,
    build_today_injury_index,
    fetch_season_transactions,
    is_injured_as_of,
    key_injuries_as_of,
    parse_il_events,
)

YANKEES = 147
RED_SOX = 111


def _transaction(description: str, player_id: int, team_id: int, event_date: str, name: str = "Player Name") -> dict:
    return {
        "description": description, "date": event_date,
        "person": {"id": player_id, "fullName": name},
        "toTeam": {"id": team_id},
    }


def _stats_response(stat: dict) -> Mock:
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {"stats": [{"splits": [{"stat": stat}]}]}
    return resp


def test_parse_il_events_recognizes_placement():
    transactions = [_transaction(
        "Oakland Athletics placed CF Skye Bolt on the 10-day injured list. Right oblique strain.",
        player_id=621450, team_id=133, event_date="2026-04-10",
    )]
    events = parse_il_events(transactions)
    assert len(events) == 1
    assert events[0].kind == "placed"
    assert events[0].is_pitcher is False


def test_parse_il_events_recognizes_activation():
    transactions = [_transaction(
        "San Francisco Giants activated C Joey Bart from the 10-day injured list.",
        player_id=663698, team_id=137, event_date="2026-04-10",
    )]
    assert parse_il_events(transactions)[0].kind == "activated"


def test_parse_il_events_ignores_transfer_between_il_tiers():
    transactions = [_transaction(
        "Cleveland Guardians transferred RHP Shane Bieber from the 15-day injured list to the 60-day injured list.",
        player_id=669456, team_id=114, event_date="2026-04-10",
    )]
    assert parse_il_events(transactions) == []


def test_parse_il_events_detects_pitcher_from_position_abbreviation():
    transactions = [_transaction(
        "Washington Nationals placed RHP Mason Thompson on the 10-day injured list.",
        player_id=666168, team_id=120, event_date="2026-04-10",
    )]
    assert parse_il_events(transactions)[0].is_pitcher is True


def test_parse_il_events_skips_malformed_entries():
    assert parse_il_events([{"description": "placed on the injured list"}]) == []


def test_build_injury_index_marks_hitter_key_when_over_pa_threshold():
    events = [ILEvent(player_id=1, player_name="Star Hitter", team_id=YANKEES, event_date="2026-04-10", kind="placed", is_pitcher=False)]
    with patch("jsa.data_sources.injuries.session.get", return_value=_stats_response({"plateAppearances": 80})):
        index = build_injury_index(events)
    assert index.is_key_by_player[1] is True


def test_build_injury_index_marks_hitter_not_key_when_under_pa_threshold():
    events = [ILEvent(player_id=1, player_name="Bench Player", team_id=YANKEES, event_date="2026-04-10", kind="placed", is_pitcher=False)]
    with patch("jsa.data_sources.injuries.session.get", return_value=_stats_response({"plateAppearances": 20})):
        index = build_injury_index(events)
    assert index.is_key_by_player[1] is False


def test_build_injury_index_marks_pitcher_key_when_over_ip_threshold():
    events = [ILEvent(player_id=2, player_name="Rotation Starter", team_id=YANKEES, event_date="2026-04-10", kind="placed", is_pitcher=True)]
    with patch("jsa.data_sources.injuries.session.get", return_value=_stats_response({"inningsPitched": "18.0"})):
        index = build_injury_index(events)
    assert index.is_key_by_player[2] is True


def test_build_injury_index_pitcher_not_key_when_under_ip_threshold():
    events = [ILEvent(player_id=2, player_name="Mop-up Reliever", team_id=YANKEES, event_date="2026-04-10", kind="placed", is_pitcher=True)]
    with patch("jsa.data_sources.injuries.session.get", return_value=_stats_response({"inningsPitched": "3.0"})):
        index = build_injury_index(events)
    assert index.is_key_by_player[2] is False


def test_build_injury_index_activation_only_is_never_key():
    events = [ILEvent(player_id=3, player_name="X", team_id=YANKEES, event_date="2026-04-10", kind="activated", is_pitcher=False)]
    index = build_injury_index(events)  # nunca deberia pegarle a la red -- sin "placed" no hay fecha que evaluar
    assert index.is_key_by_player[3] is False


def test_is_injured_as_of_true_after_placement_before_activation():
    events = [ILEvent(player_id=1, player_name="X", team_id=YANKEES, event_date="2026-04-10", kind="placed", is_pitcher=False)]
    with patch("jsa.data_sources.injuries.session.get", return_value=_stats_response({"plateAppearances": 80})):
        index = build_injury_index(events)
    assert is_injured_as_of(index, 1, "2026-04-20") is True
    assert is_injured_as_of(index, 1, "2026-04-10") is False  # antes del propio evento, no incluido


def test_is_injured_as_of_false_after_activation():
    events = [
        ILEvent(player_id=1, player_name="X", team_id=YANKEES, event_date="2026-04-10", kind="placed", is_pitcher=False),
        ILEvent(player_id=1, player_name="X", team_id=YANKEES, event_date="2026-04-25", kind="activated", is_pitcher=False),
    ]
    with patch("jsa.data_sources.injuries.session.get", return_value=_stats_response({"plateAppearances": 80})):
        index = build_injury_index(events)
    assert is_injured_as_of(index, 1, "2026-04-20") is True
    assert is_injured_as_of(index, 1, "2026-05-01") is False


def test_is_injured_as_of_false_for_unknown_player():
    index = build_injury_index([])
    assert is_injured_as_of(index, 999, "2026-04-20") is False
    assert is_injured_as_of(index, None, "2026-04-20") is False


def test_key_injuries_as_of_filters_by_team_and_key_status():
    events = [
        ILEvent(player_id=1, player_name="Key Yankee", team_id=YANKEES, event_date="2026-04-10", kind="placed", is_pitcher=False),
        ILEvent(player_id=2, player_name="Bench Yankee", team_id=YANKEES, event_date="2026-04-10", kind="placed", is_pitcher=False),
        ILEvent(player_id=3, player_name="Key Red Sox", team_id=RED_SOX, event_date="2026-04-10", kind="placed", is_pitcher=False),
    ]

    def side_effect(url, params=None, timeout=None):
        pa_by_player = {1: 80, 2: 10, 3: 80}
        player_id = int(url.rsplit("/", 2)[1])
        return _stats_response({"plateAppearances": pa_by_player[player_id]})

    with patch("jsa.data_sources.injuries.session.get", side_effect=side_effect):
        index = build_injury_index(events)

    assert key_injuries_as_of(index, YANKEES, "2026-04-20") == ["Key Yankee"]
    assert key_injuries_as_of(index, RED_SOX, "2026-04-20") == ["Key Red Sox"]


def test_fetch_season_transactions_returns_list_on_success():
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {"transactions": [{"date": "2026-04-10"}]}
    with patch("jsa.data_sources.injuries.session.get", return_value=resp):
        result = fetch_season_transactions(2026)
    assert result == [{"date": "2026-04-10"}]


def test_fetch_season_transactions_returns_empty_list_on_failure():
    with patch("jsa.data_sources.injuries.session.get", side_effect=requests.RequestException("boom")):
        assert fetch_season_transactions(2026) == []


def test_build_today_injury_index_end_to_end_with_mocked_network():
    transactions_resp = Mock()
    transactions_resp.raise_for_status = Mock()
    transactions_resp.json.return_value = {"transactions": [_transaction(
        "New York Yankees placed 1B Star Hitter on the 10-day injured list.",
        player_id=1, team_id=YANKEES, event_date="2026-04-10", name="Star Hitter",
    )]}
    stats_resp = _stats_response({"plateAppearances": 80})

    def side_effect(url, params=None, timeout=None):
        return transactions_resp if url.endswith("/transactions") else stats_resp

    with patch("jsa.data_sources.injuries.session.get", side_effect=side_effect):
        index = build_today_injury_index(2026)

    assert key_injuries_as_of(index, YANKEES, "2026-04-20") == ["Star Hitter"]
