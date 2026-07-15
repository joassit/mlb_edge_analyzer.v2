"""`jsa/data_sources/stats.py::get_team_fielding_pct()` -- version de
PRODUCCION de la senal defensiva de team_quality, mismo patron que
`get_team_ops()`. HTTP mockeado sobre `jsa.data_sources.stats.session` --
nunca red real."""

from __future__ import annotations

from unittest.mock import Mock, patch

import requests

from jsa.data_sources import stats


def _fielding_response(fielding: str) -> Mock:
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {"stats": [{"splits": [{"stat": {"fielding": fielding}}]}]}
    return resp


def test_get_team_fielding_pct_parses_the_fielding_field():
    with patch("jsa.data_sources.stats.session.get", return_value=_fielding_response(".988")):
        result = stats.get_team_fielding_pct(team_id=147, season=2026)
    assert result == 0.988


def test_get_team_fielding_pct_none_when_no_splits():
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {"stats": [{"splits": []}]}
    with patch("jsa.data_sources.stats.session.get", return_value=resp):
        assert stats.get_team_fielding_pct(team_id=147, season=2026) is None


def test_get_team_fielding_pct_none_on_request_failure():
    with patch("jsa.data_sources.stats.session.get", side_effect=requests.RequestException("boom")):
        assert stats.get_team_fielding_pct(team_id=147, season=2026) is None
