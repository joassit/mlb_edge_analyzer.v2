"""`historical/point_in_time_provider.py::_parse_era()` -- MLB Stats API a
veces devuelve `"-.--"` como ERA indefinido (pitcher con 0 IP pero una
carrera cargada). Bug real encontrado en la re-ingesta de 2022 (1 de 2740
juegos abortado con `ValueError: could not convert string to float: '-.--'`
en `pitcher_era_ip_as_of()`) -- nunca debe tumbar la reconstruccion de un
juego entero, se trata como "sin dato"."""

from __future__ import annotations

from unittest.mock import Mock, patch

from jsa.historical.point_in_time_provider import MLBStatsAPIProvider, _parse_era


def test_parse_era_valid_string():
    assert _parse_era("3.45") == 3.45


def test_parse_era_undefined_placeholder_is_none():
    assert _parse_era("-.--") is None


def test_parse_era_none_is_none():
    assert _parse_era(None) is None


def _stat_response(stat: dict) -> Mock:
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {"stats": [{"splits": [{"stat": stat}]}]}
    return resp


def test_pitcher_era_ip_as_of_skips_undefined_era_without_crashing():
    provider = MLBStatsAPIProvider()
    stat = _stat_response({"era": "-.--", "inningsPitched": "0.0", "gamesStarted": 0})
    with patch("jsa.historical.point_in_time_provider.session.get", return_value=stat):
        result = provider.pitcher_era_ip_as_of(543037, "2022-04-15", 2022)
    assert result is None


def test_pitcher_era_ip_as_of_still_works_for_valid_era():
    provider = MLBStatsAPIProvider()
    stat = _stat_response({"era": "3.00", "inningsPitched": "40.0", "gamesStarted": 7})
    with patch("jsa.historical.point_in_time_provider.session.get", return_value=stat):
        result = provider.pitcher_era_ip_as_of(543037, "2022-04-15", 2022)
    assert result == {"era": 3.0, "ip": 40.0, "projected_ip": 40.0 / 7}


def test_bullpen_era_as_of_skips_pitcher_with_undefined_era_without_crashing():
    provider = MLBStatsAPIProvider()
    roster_resp = Mock()
    roster_resp.raise_for_status = Mock()
    roster_resp.json.return_value = {
        "roster": [{"person": {"id": 100}, "position": {"abbreviation": "P"}},
                   {"person": {"id": 200}, "position": {"abbreviation": "P"}}]
    }
    undefined_era = _stat_response({"era": "-.--", "inningsPitched": "0.0", "saves": 0})
    valid_era = _stat_response({"era": "2.50", "inningsPitched": "30.0", "saves": 10})

    def side_effect(url, params=None, timeout=None):
        if url.endswith("/roster"):
            return roster_resp
        pid = int(url.rsplit("/", 2)[1])
        return undefined_era if pid == 100 else valid_era

    with patch("jsa.historical.point_in_time_provider.session.get", side_effect=side_effect):
        result = provider.bullpen_era_as_of(147, "2022-04-15", 2022)

    # El pitcher con ERA indefinido se ignora del promedio ponderado --
    # no aborta el calculo del resto del bullpen.
    assert result["era"] == 2.50
    assert result["closer_pitcher_id"] == 200
