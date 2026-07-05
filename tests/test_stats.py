import data.stats as stats
from data.stats import _parse_innings


def test_parse_innings_with_thirds():
    assert abs(_parse_innings("63.1") - 63.3333) < 0.001
    assert abs(_parse_innings("63.2") - 63.6667) < 0.001


def test_parse_innings_whole_numbers():
    assert _parse_innings("63.0") == 63.0
    assert _parse_innings("10") == 10.0


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_get_pitcher_era_ip_parses_era_and_innings(monkeypatch):
    payload = {"stats": [{"splits": [{"stat": {"era": "3.45", "inningsPitched": "100.1"}}]}]}
    monkeypatch.setattr(stats.session, "get", lambda *a, **k: _FakeResponse(payload))
    monkeypatch.setattr(stats, "_pitcher_stats_cache", {})

    result = stats.get_pitcher_era_ip(pitcher_id=12345, season=2026)

    assert result is not None
    era, ip = result
    assert era == 3.45
    assert abs(ip - 100.3333) < 0.001


def test_get_pitcher_era_ip_returns_none_without_splits(monkeypatch):
    payload = {"stats": [{"splits": []}]}
    monkeypatch.setattr(stats.session, "get", lambda *a, **k: _FakeResponse(payload))
    monkeypatch.setattr(stats, "_pitcher_stats_cache", {})

    assert stats.get_pitcher_era_ip(pitcher_id=99999, season=2026) is None


def test_get_league_ops_is_weighted_by_plate_appearances(monkeypatch):
    # Un bateador con 600 PA a .900 OPS debe pesar mucho más que uno con
    # apenas 100 PA (el mínimo para calificar) a .600 OPS -- el promedio
    # ponderado debe quedar mucho más cerca de .900 que un promedio simple.
    payload = {
        "stats": [{"splits": [
            {"stat": {"plateAppearances": 600, "ops": 0.900}},
            {"stat": {"plateAppearances": 100, "ops": 0.600}},
        ]}]
    }
    monkeypatch.setattr(stats.session, "get", lambda *a, **k: _FakeResponse(payload))
    monkeypatch.setattr(stats, "_league_ops_cache", None)

    league_ops = stats.get_league_ops(season=2026)

    simple_mean = (0.900 + 0.600) / 2
    expected_weighted = (0.900 * 600 + 0.600 * 100) / (600 + 100)
    assert abs(league_ops - expected_weighted) < 1e-9
    assert league_ops > simple_mean  # el ponderado se acerca más al bateador de más PA


def test_get_league_ops_excludes_batters_below_min_pa(monkeypatch):
    payload = {
        "stats": [{"splits": [
            {"stat": {"plateAppearances": 600, "ops": 0.900}},
            {"stat": {"plateAppearances": 5, "ops": 2.000}},  # muestra irrisoria, debe excluirse
        ]}]
    }
    monkeypatch.setattr(stats.session, "get", lambda *a, **k: _FakeResponse(payload))
    monkeypatch.setattr(stats, "_league_ops_cache", None)

    league_ops = stats.get_league_ops(season=2026)

    assert abs(league_ops - 0.900) < 1e-9
