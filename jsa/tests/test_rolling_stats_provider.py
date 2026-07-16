"""`historical/point_in_time_provider.py::team_ops_rolling_as_of()` /
`team_era_rolling_as_of()` -- candidatos de forma reciente para el pilar
Trend (Seccion trend, hoy un stub). Verifica que la ventana de fecha
pedida a la API sea `[as_of_date - days, as_of_date - 1]` -- nunca
incluye el propio dia de corte, mismo criterio point-in-time que el
resto del proveedor. Nunca red real."""

from __future__ import annotations

from unittest.mock import Mock, patch

from jsa.historical.point_in_time_provider import MLBStatsAPIProvider


def _stat_response(stat: dict) -> Mock:
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {"stats": [{"splits": [{"stat": stat}]}]}
    return resp


def test_team_ops_rolling_as_of_uses_correct_date_window():
    provider = MLBStatsAPIProvider()
    resp = _stat_response({"ops": "0.812"})
    with patch("jsa.historical.point_in_time_provider.session.get", return_value=resp) as mock_get:
        result = provider.team_ops_rolling_as_of(147, "2022-04-15", 7)
    assert result == 0.812
    params = mock_get.call_args.kwargs["params"]
    assert params["startDate"] == "2022-04-08"
    assert params["endDate"] == "2022-04-14"  # nunca incluye el propio dia de corte
    assert params["group"] == "hitting"


def test_team_ops_rolling_as_of_14_days_window():
    provider = MLBStatsAPIProvider()
    resp = _stat_response({"ops": "0.700"})
    with patch("jsa.historical.point_in_time_provider.session.get", return_value=resp) as mock_get:
        provider.team_ops_rolling_as_of(147, "2022-04-15", 14)
    params = mock_get.call_args.kwargs["params"]
    assert params["startDate"] == "2022-04-01"
    assert params["endDate"] == "2022-04-14"


def test_team_ops_rolling_as_of_no_splits_returns_none():
    provider = MLBStatsAPIProvider()
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {"stats": [{"splits": []}]}
    with patch("jsa.historical.point_in_time_provider.session.get", return_value=resp):
        assert provider.team_ops_rolling_as_of(147, "2022-04-15", 7) is None


def test_team_era_rolling_as_of_uses_correct_date_window_and_group():
    provider = MLBStatsAPIProvider()
    resp = _stat_response({"era": "3.25"})
    with patch("jsa.historical.point_in_time_provider.session.get", return_value=resp) as mock_get:
        result = provider.team_era_rolling_as_of(147, "2022-04-15", 7)
    assert result == 3.25
    params = mock_get.call_args.kwargs["params"]
    assert params["startDate"] == "2022-04-08"
    assert params["endDate"] == "2022-04-14"
    assert params["group"] == "pitching"


def test_team_era_rolling_as_of_undefined_era_placeholder_is_none():
    """Mismo bug real que _parse_era ya protege en pitcher_era_ip_as_of --
    la API devuelve '-.--' cuando el ERA queda indefinido en la ventana."""
    provider = MLBStatsAPIProvider()
    resp = _stat_response({"era": "-.--"})
    with patch("jsa.historical.point_in_time_provider.session.get", return_value=resp):
        assert provider.team_era_rolling_as_of(147, "2022-04-15", 7) is None
