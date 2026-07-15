"""`jsa/historical/injuries.py` -- parseo de transacciones, criterio de
"lesion clave" (Seccion team_quality) e indice point-in-time. Todo puro
salvo `build_injury_index()`, que se testea con un `FakeInjuryProvider`
deterministico (sin red)."""

from __future__ import annotations

from jsa.historical.injuries import (
    ILEvent,
    build_injury_index,
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


class FakeInjuryProvider:
    """PA/IP reciente fijo por jugador -- controla exactamente quien cruza
    el umbral de "lesion clave" sin pegarle a la red."""

    def __init__(self, pa_by_player: dict[int, int] | None = None, ip_by_player: dict[int, float] | None = None):
        self.pa_by_player = pa_by_player or {}
        self.ip_by_player = ip_by_player or {}

    def hitter_recent_pa_as_of(self, player_id, as_of_date, days=30):
        return self.pa_by_player.get(player_id)

    def pitcher_recent_ip_as_of(self, player_id, as_of_date, days=30):
        return self.ip_by_player.get(player_id)


def test_parse_il_events_recognizes_placement():
    transactions = [_transaction(
        "Oakland Athletics placed CF Skye Bolt on the 10-day injured list. Right oblique strain.",
        player_id=621450, team_id=133, event_date="2022-04-10",
    )]
    events = parse_il_events(transactions)
    assert len(events) == 1
    assert events[0].kind == "placed"
    assert events[0].player_id == 621450
    assert events[0].is_pitcher is False  # "CF", no RHP/LHP/P


def test_parse_il_events_recognizes_activation():
    transactions = [_transaction(
        "San Francisco Giants activated C Joey Bart from the 10-day injured list.",
        player_id=663698, team_id=137, event_date="2023-04-10",
    )]
    events = parse_il_events(transactions)
    assert events[0].kind == "activated"


def test_parse_il_events_ignores_transfer_between_il_tiers():
    transactions = [_transaction(
        "Cleveland Guardians transferred RHP Shane Bieber from the 15-day injured list to the 60-day injured list.",
        player_id=669456, team_id=114, event_date="2024-04-10",
    )]
    events = parse_il_events(transactions)
    assert events == []  # sigue lesionado, no es un cambio de estado que trackear


def test_parse_il_events_detects_pitcher_from_position_abbreviation():
    transactions = [_transaction(
        "Washington Nationals placed RHP Mason Thompson on the 10-day injured list.",
        player_id=666168, team_id=120, event_date="2022-04-10",
    )]
    events = parse_il_events(transactions)
    assert events[0].is_pitcher is True


def test_parse_il_events_skips_unrelated_transactions():
    transactions = [_transaction("New York Yankees selected the contract of Player X.", player_id=1, team_id=147, event_date="2022-04-10")]
    assert parse_il_events(transactions) == []


def test_parse_il_events_skips_malformed_entries():
    transactions = [{"description": "placed on the injured list"}]  # sin person/toTeam/date
    assert parse_il_events(transactions) == []


def test_build_injury_index_marks_hitter_key_when_over_pa_threshold():
    events = [ILEvent(player_id=1, player_name="Star Hitter", team_id=YANKEES, event_date="2022-04-10", kind="placed", is_pitcher=False)]
    provider = FakeInjuryProvider(pa_by_player={1: 80})  # >= 50
    index = build_injury_index(events, provider)
    assert index.is_key_by_player[1] is True


def test_build_injury_index_marks_hitter_not_key_when_under_pa_threshold():
    events = [ILEvent(player_id=1, player_name="Bench Player", team_id=YANKEES, event_date="2022-04-10", kind="placed", is_pitcher=False)]
    provider = FakeInjuryProvider(pa_by_player={1: 20})  # < 50
    index = build_injury_index(events, provider)
    assert index.is_key_by_player[1] is False


def test_build_injury_index_marks_pitcher_key_when_over_ip_threshold():
    events = [ILEvent(player_id=2, player_name="Rotation Starter", team_id=YANKEES, event_date="2022-04-10", kind="placed", is_pitcher=True)]
    provider = FakeInjuryProvider(ip_by_player={2: 18.0})  # >= 15
    index = build_injury_index(events, provider)
    assert index.is_key_by_player[2] is True


def test_build_injury_index_pitcher_not_key_when_under_ip_threshold():
    events = [ILEvent(player_id=2, player_name="Mop-up Reliever", team_id=YANKEES, event_date="2022-04-10", kind="placed", is_pitcher=True)]
    provider = FakeInjuryProvider(ip_by_player={2: 3.0})  # < 15
    index = build_injury_index(events, provider)
    assert index.is_key_by_player[2] is False


def test_build_injury_index_activation_only_is_never_key():
    # Un jugador que solo aparece "activated" (sin "placed" previo en la
    # ventana de la temporada, ej. se lesiono en la temporada anterior) no
    # tiene fecha de colocacion que evaluar.
    events = [ILEvent(player_id=3, player_name="X", team_id=YANKEES, event_date="2022-04-10", kind="activated", is_pitcher=False)]
    index = build_injury_index(events, FakeInjuryProvider())
    assert index.is_key_by_player[3] is False


def test_is_injured_as_of_true_after_placement_before_activation():
    events = [ILEvent(player_id=1, player_name="X", team_id=YANKEES, event_date="2022-04-10", kind="placed", is_pitcher=False)]
    index = build_injury_index(events, FakeInjuryProvider(pa_by_player={1: 80}))
    assert is_injured_as_of(index, 1, "2022-04-20") is True
    assert is_injured_as_of(index, 1, "2022-04-10") is False  # antes del propio evento, no incluido


def test_is_injured_as_of_false_after_activation():
    events = [
        ILEvent(player_id=1, player_name="X", team_id=YANKEES, event_date="2022-04-10", kind="placed", is_pitcher=False),
        ILEvent(player_id=1, player_name="X", team_id=YANKEES, event_date="2022-04-25", kind="activated", is_pitcher=False),
    ]
    index = build_injury_index(events, FakeInjuryProvider(pa_by_player={1: 80}))
    assert is_injured_as_of(index, 1, "2022-04-20") is True  # todavia lesionado
    assert is_injured_as_of(index, 1, "2022-05-01") is False  # ya activado


def test_is_injured_as_of_false_for_unknown_player():
    index = build_injury_index([], FakeInjuryProvider())
    assert is_injured_as_of(index, 999, "2022-04-20") is False
    assert is_injured_as_of(index, None, "2022-04-20") is False


def test_key_injuries_as_of_filters_by_team_and_key_status():
    events = [
        ILEvent(player_id=1, player_name="Key Yankee", team_id=YANKEES, event_date="2022-04-10", kind="placed", is_pitcher=False),
        ILEvent(player_id=2, player_name="Bench Yankee", team_id=YANKEES, event_date="2022-04-10", kind="placed", is_pitcher=False),
        ILEvent(player_id=3, player_name="Key Red Sox", team_id=RED_SOX, event_date="2022-04-10", kind="placed", is_pitcher=False),
    ]
    provider = FakeInjuryProvider(pa_by_player={1: 80, 2: 10, 3: 80})
    index = build_injury_index(events, provider)

    yankees_injuries = key_injuries_as_of(index, YANKEES, "2022-04-20")
    assert yankees_injuries == ["Key Yankee"]  # Bench Yankee no cruza el umbral

    red_sox_injuries = key_injuries_as_of(index, RED_SOX, "2022-04-20")
    assert red_sox_injuries == ["Key Red Sox"]


def test_key_injuries_as_of_excludes_players_already_activated():
    events = [
        ILEvent(player_id=1, player_name="Key Yankee", team_id=YANKEES, event_date="2022-04-10", kind="placed", is_pitcher=False),
        ILEvent(player_id=1, player_name="Key Yankee", team_id=YANKEES, event_date="2022-04-15", kind="activated", is_pitcher=False),
    ]
    index = build_injury_index(events, FakeInjuryProvider(pa_by_player={1: 80}))
    assert key_injuries_as_of(index, YANKEES, "2022-04-20") == []
