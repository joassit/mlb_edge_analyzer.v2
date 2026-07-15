"""`travel_distance` (Seccion 5: "el visitante es quien viaja") -- cubre
las 3 piezas nuevas: `park_factors.distance_miles()` (pura), `ingestion.
build_previous_park_index()` (pura, sobre el schedule ya fetcheado) y el
lado de produccion (`mlb_api.get_previous_game_location()` +
`data_sources.travel.preload_travel_distances()`, ambos con HTTP
mockeado)."""

from __future__ import annotations

from datetime import date
from unittest.mock import Mock, patch

import pytest

from jsa.data_sources import mlb_api, park_factors, travel
from jsa.historical.ingestion import build_previous_park_index

YANKEE_STADIUM = 147  # New York Yankees
FENWAY_PARK = 111  # Boston Red Sox
DODGER_STADIUM = 119  # Los Angeles Dodgers


def test_distance_miles_between_known_parks_is_realistic():
    # Yankee Stadium <-> Fenway Park: ~190 millas en linea recta -- rango
    # amplio a proposito, no una cifra exacta fragil ante redondeos.
    d = park_factors.distance_miles(YANKEE_STADIUM, FENWAY_PARK)
    assert 170 <= d <= 210


def test_distance_miles_cross_country_is_much_larger():
    coast_to_coast = park_factors.distance_miles(YANKEE_STADIUM, DODGER_STADIUM)
    short_hop = park_factors.distance_miles(YANKEE_STADIUM, FENWAY_PARK)
    assert coast_to_coast > short_hop * 5


def test_distance_miles_same_team_is_zero():
    assert park_factors.distance_miles(YANKEE_STADIUM, YANKEE_STADIUM) == 0.0


def test_distance_miles_is_symmetric():
    a_to_b = park_factors.distance_miles(YANKEE_STADIUM, DODGER_STADIUM)
    b_to_a = park_factors.distance_miles(DODGER_STADIUM, YANKEE_STADIUM)
    assert a_to_b == pytest.approx(b_to_a)


def test_distance_miles_none_if_either_id_missing():
    assert park_factors.distance_miles(None, FENWAY_PARK) is None
    assert park_factors.distance_miles(YANKEE_STADIUM, None) is None


def test_distance_miles_none_for_unknown_team_id():
    assert park_factors.distance_miles(YANKEE_STADIUM, 999999) is None


def _game(game_pk, game_date, home_team_id, away_team_id):
    return {"game_pk": game_pk, "game_date": game_date, "home_team_id": home_team_id, "away_team_id": away_team_id}


def test_build_previous_park_index_chains_chronologically():
    games = [
        _game(1, "2022-04-01", home_team_id=147, away_team_id=111),  # Yankees en casa vs Red Sox
        _game(2, "2022-04-05", home_team_id=111, away_team_id=147),  # Red Sox en casa vs Yankees
        _game(3, "2022-04-10", home_team_id=147, away_team_id=111),  # Yankees en casa otra vez
    ]
    index = build_previous_park_index(games)
    # Juego 1: primer partido de la temporada para ambos -- ausente del indice.
    assert (147, 1) not in index
    assert (111, 1) not in index
    # Juego 2: Yankees (visitante) venian de jugar en su propia casa (juego 1).
    assert index[(147, 2)] == 147
    # Juego 3: Red Sox (visitante) venian de jugar en Boston (juego 2, su propia casa).
    assert index[(111, 3)] == 111


def test_build_previous_park_index_doubleheader_same_park_is_zero_distance():
    games = [
        _game(1, "2022-04-01", home_team_id=147, away_team_id=111),
        _game(2, "2022-04-01", home_team_id=147, away_team_id=111),  # doble cartelera, mismo dia
    ]
    index = build_previous_park_index(games)
    assert index[(111, 2)] == 147  # visitante viene del mismo estadio -> distancia 0 via distance_miles


def test_build_previous_park_index_skips_malformed_entries_without_crashing():
    games = [
        _game(1, "2022-04-01", home_team_id=147, away_team_id=111),
        {"game_pk": 2, "season": 2022},  # sin game_date/home_team_id/away_team_id
        _game(3, "2022-04-10", home_team_id=147, away_team_id=111),
    ]
    index = build_previous_park_index(games)
    assert index[(111, 3)] == 147


def test_build_previous_park_index_empty_games_returns_empty_index():
    assert build_previous_park_index([]) == {}


def _mock_transactions_response(games_by_date: dict[str, list[dict]]) -> Mock:
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {
        "dates": [{"date": d, "games": gs} for d, gs in games_by_date.items()]
    }
    return resp


def _final_game(home_team_id: int) -> dict:
    return {"status": {"abstractGameState": "Final"}, "teams": {"home": {"team": {"id": home_team_id}}}}


def test_get_previous_game_location_picks_most_recent_final_game():
    payload = _mock_transactions_response({
        "2022-04-05": [_final_game(home_team_id=147)],
        "2022-04-08": [_final_game(home_team_id=111)],
    })
    with patch("jsa.data_sources.mlb_api.session.get", return_value=payload):
        result = mlb_api.get_previous_game_location(111, date(2022, 4, 10))
    assert result == 111


def test_get_previous_game_location_ignores_non_final_games():
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {"dates": [{"date": "2022-04-09", "games": [
        {"status": {"abstractGameState": "Postponed"}, "teams": {"home": {"team": {"id": 111}}}}
    ]}]}
    with patch("jsa.data_sources.mlb_api.session.get", return_value=resp):
        result = mlb_api.get_previous_game_location(111, date(2022, 4, 10))
    assert result is None


def test_get_previous_game_location_none_when_no_games_in_window():
    payload = _mock_transactions_response({})
    with patch("jsa.data_sources.mlb_api.session.get", return_value=payload):
        result = mlb_api.get_previous_game_location(111, date(2022, 4, 10))
    assert result is None


def test_get_previous_game_location_none_on_request_failure():
    import requests

    with patch("jsa.data_sources.mlb_api.session.get", side_effect=requests.RequestException("boom")):
        result = mlb_api.get_previous_game_location(111, date(2022, 4, 10))
    assert result is None


def test_preload_travel_distances_uses_todays_home_team_not_away_teams_own_park():
    games = [{"game_pk": 1, "home_team_id": YANKEE_STADIUM, "away_team_id": FENWAY_PARK}]
    with patch("jsa.data_sources.mlb_api.get_previous_game_location", return_value=DODGER_STADIUM):
        result = travel.preload_travel_distances(games, date(2022, 4, 10))
    # El visitante (Red Sox) venia de LA -- la distancia debe ser hasta el
    # estadio de HOY (Yankee Stadium), no hasta el propio Fenway.
    expected = park_factors.distance_miles(DODGER_STADIUM, YANKEE_STADIUM)
    assert result[FENWAY_PARK] == pytest.approx(expected)


def test_preload_travel_distances_none_when_no_previous_game_found():
    games = [{"game_pk": 1, "home_team_id": YANKEE_STADIUM, "away_team_id": FENWAY_PARK}]
    with patch("jsa.data_sources.mlb_api.get_previous_game_location", return_value=None):
        result = travel.preload_travel_distances(games, date(2022, 4, 10))
    assert result[FENWAY_PARK] is None


def test_preload_travel_distances_isolates_per_team_failures():
    games = [
        {"game_pk": 1, "home_team_id": YANKEE_STADIUM, "away_team_id": FENWAY_PARK},
        {"game_pk": 2, "home_team_id": DODGER_STADIUM, "away_team_id": YANKEE_STADIUM},
    ]

    def side_effect(team_id, before_date, **kwargs):
        if team_id == FENWAY_PARK:
            raise RuntimeError("boom")
        return DODGER_STADIUM

    with patch("jsa.data_sources.mlb_api.get_previous_game_location", side_effect=side_effect):
        result = travel.preload_travel_distances(games, date(2022, 4, 10))
    assert result[FENWAY_PARK] is None  # aislado, no tumba el resto
    assert result[YANKEE_STADIUM] is not None
