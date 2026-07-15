"""`jsa/data_sources/stats.py::get_bullpen_era()` -- ahora devuelve
{"era", "closer_pitcher_id"} en vez de solo un float; el cerrador se
identifica DENTRO del mismo loop de fetch de relevistas (mas saves
point-in-time del roster), sin trafico de red adicional. HTTP mockeado
sobre `jsa.data_sources.stats.session` -- nunca red real."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from jsa.config import FALLBACK_BULLPEN_ERA
from jsa.data_sources import stats


def _roster_response(pitcher_ids: list[int]) -> Mock:
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {
        "roster": [{"person": {"id": pid}, "position": {"abbreviation": "P"}} for pid in pitcher_ids]
    }
    return resp


def _pitcher_stats_response(era: float, ip: str, saves: int | None, games=20, starts=0) -> Mock:
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {
        "stats": [{"splits": [{"stat": {
            "era": era, "inningsPitched": ip, "saves": saves, "gamesPlayed": games, "gamesStarted": starts,
        }}]}]
    }
    return resp


@pytest.fixture(autouse=True)
def _clear_bullpen_cache():
    stats._bullpen_cache.clear()
    yield
    stats._bullpen_cache.clear()


def test_get_bullpen_era_identifies_closer_by_most_saves():
    roster = _roster_response([100, 200, 300])
    pitcher_stats = {
        100: _pitcher_stats_response(era=3.00, ip="40.0", saves=2),
        200: _pitcher_stats_response(era=2.50, ip="45.0", saves=30),  # el cerrador
        300: _pitcher_stats_response(era=4.00, ip="35.0", saves=0),
    }

    def side_effect(url, params=None, timeout=None):
        if url.endswith("/roster"):
            return roster
        pid = int(url.rsplit("/", 2)[1])
        return pitcher_stats[pid]

    with patch("jsa.data_sources.stats.session.get", side_effect=side_effect):
        result = stats.get_bullpen_era(team_id=999, season=2026)

    assert result["closer_pitcher_id"] == 200


def test_get_bullpen_era_no_closer_when_nobody_has_saves():
    roster = _roster_response([100, 200])
    pitcher_stats = {
        100: _pitcher_stats_response(era=3.00, ip="40.0", saves=0),
        200: _pitcher_stats_response(era=4.00, ip="35.0", saves=None),
    }

    def side_effect(url, params=None, timeout=None):
        if url.endswith("/roster"):
            return roster
        pid = int(url.rsplit("/", 2)[1])
        return pitcher_stats[pid]

    with patch("jsa.data_sources.stats.session.get", side_effect=side_effect):
        result = stats.get_bullpen_era(team_id=999, season=2026)

    assert result["closer_pitcher_id"] is None


def test_get_bullpen_era_still_computes_ip_weighted_era():
    roster = _roster_response([100, 200])
    pitcher_stats = {
        100: _pitcher_stats_response(era=2.00, ip="30.0", saves=15),
        200: _pitcher_stats_response(era=6.00, ip="10.0", saves=0),
    }

    def side_effect(url, params=None, timeout=None):
        if url.endswith("/roster"):
            return roster
        pid = int(url.rsplit("/", 2)[1])
        return pitcher_stats[pid]

    with patch("jsa.data_sources.stats.session.get", side_effect=side_effect):
        result = stats.get_bullpen_era(team_id=999, season=2026)

    expected = (2.00 * 30.0 + 6.00 * 10.0) / 40.0
    assert result["era"] == pytest.approx(expected)
    assert result["closer_pitcher_id"] == 100


def test_get_bullpen_era_falls_back_when_roster_fetch_fails():
    import requests

    with patch("jsa.data_sources.stats.session.get", side_effect=requests.RequestException("boom")):
        result = stats.get_bullpen_era(team_id=999, season=2026)

    assert result == {"era": FALLBACK_BULLPEN_ERA, "closer_pitcher_id": None}


def test_get_bullpen_era_excludes_pitchers_classified_as_starters():
    roster = _roster_response([100, 200])
    starter = _pitcher_stats_response(era=3.00, ip="150.0", saves=0, games=28, starts=28)
    reliever = _pitcher_stats_response(era=4.00, ip="40.0", saves=20, games=50, starts=0)

    def side_effect(url, params=None, timeout=None):
        if url.endswith("/roster"):
            return roster
        pid = int(url.rsplit("/", 2)[1])
        return starter if pid == 100 else reliever

    with patch("jsa.data_sources.stats.session.get", side_effect=side_effect):
        result = stats.get_bullpen_era(team_id=999, season=2026)

    # El "starter" (28 de 28 juegos como abridor) queda excluido del bullpen ERA.
    assert result["era"] == pytest.approx(4.00)
    assert result["closer_pitcher_id"] == 200


def test_get_bullpen_era_is_cached_per_team_and_season():
    roster = _roster_response([100])
    pitcher = _pitcher_stats_response(era=3.00, ip="40.0", saves=25)
    calls = {"count": 0}

    def side_effect(url, params=None, timeout=None):
        calls["count"] += 1
        return roster if url.endswith("/roster") else pitcher

    with patch("jsa.data_sources.stats.session.get", side_effect=side_effect):
        first = stats.get_bullpen_era(team_id=999, season=2026)
        second = stats.get_bullpen_era(team_id=999, season=2026)

    assert first == second
    assert calls["count"] == 2  # roster + 1 pitcher, la segunda llamada usa cache
