"""
Pruebas de data/odds_api.py — mockean requests.get, nunca tocan la red real
(igual que el resto de la suite: pruebas puras, sin depender de internet).
"""

import pytest

import data.odds_api as odds_api


@pytest.fixture(autouse=True)
def isolated_cache_dir(tmp_path, monkeypatch):
    """Cada test usa su propio directorio de caché/presupuesto — sin esto,
    el caché en disco de un test contamina el siguiente (y ensuciaría el
    .cache/odds real del repo al correr la suite localmente)."""
    monkeypatch.setattr(odds_api, "ODDS_CACHE_DIR", str(tmp_path))


FAKE_PAYLOAD = [
    {
        "id": "abc123",
        "sport_key": "baseball_mlb",
        "commence_time": "2026-07-05T23:05:00Z",
        "home_team": "New York Yankees",
        "away_team": "Boston Red Sox",
        "bookmakers": [
            {
                "key": "draftkings",
                "last_update": "2026-07-05T20:00:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-07-05T20:00:00Z",
                        "outcomes": [
                            {"name": "New York Yankees", "price": -150},
                            {"name": "Boston Red Sox", "price": 130},
                        ],
                    }
                ],
            },
            {
                "key": "fanduel",
                "last_update": "2026-07-05T20:05:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-07-05T20:05:00Z",
                        "outcomes": [
                            {"name": "New York Yankees", "price": -145},
                            {"name": "Boston Red Sox", "price": 125},
                        ],
                    }
                ],
            },
        ],
    }
]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_moneyline_odds_returns_empty_without_api_key(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    assert odds_api.fetch_moneyline_odds() == []


def test_fetch_moneyline_odds_parses_expected_shape(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "fake-key-for-tests")
    monkeypatch.setattr(odds_api.requests, "get", lambda *a, **k: _FakeResponse(FAKE_PAYLOAD))

    events = odds_api.fetch_moneyline_odds()

    assert len(events) == 1
    assert events[0]["away_team"] == "Boston Red Sox"
    assert events[0]["home_team"] == "New York Yankees"
    assert len(events[0]["prices"]) == 2


def test_fetch_moneyline_odds_skips_malformed_events(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "fake-key-for-tests")
    broken_payload = [{"sport_key": "baseball_mlb"}]  # sin home_team/away_team
    monkeypatch.setattr(odds_api.requests, "get", lambda *a, **k: _FakeResponse(broken_payload))

    events = odds_api.fetch_moneyline_odds()

    assert events == []


def test_match_odds_to_game_is_case_and_whitespace_insensitive():
    events = [{"away_team": "  boston red sox ", "home_team": "New York Yankees", "prices": []}]
    match = odds_api.match_odds_to_game(events, "Boston Red Sox", "new york yankees")
    assert match is not None


def test_match_odds_to_game_returns_none_when_no_match():
    events = [{"away_team": "Boston Red Sox", "home_team": "New York Yankees", "prices": []}]
    assert odds_api.match_odds_to_game(events, "Miami Marlins", "Atlanta Braves") is None


def test_best_available_price_picks_most_favorable_odds_per_side():
    event = {
        "prices": [
            {"book": "draftkings", "away_price": 130, "home_price": -150},
            {"book": "fanduel", "away_price": 125, "home_price": -145},
        ]
    }
    best = odds_api.best_available_price(event)
    assert best == {"away": 130, "home": -145}


def test_consensus_no_vig_prob_averages_across_books():
    event = {
        "prices": [
            {"book": "draftkings", "away_price": 130, "home_price": -150},
            {"book": "fanduel", "away_price": 125, "home_price": -145},
        ]
    }
    away_p, home_p = odds_api.consensus_no_vig_prob(event)
    assert abs((away_p + home_p) - 1.0) < 1e-9
    assert 0 < away_p < home_p < 1


def test_consensus_no_vig_prob_none_without_prices():
    assert odds_api.consensus_no_vig_prob({"prices": []}) is None


def test_fetch_moneyline_odds_uses_cache_instead_of_a_second_call(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "fake-key-for-tests")
    calls = {"n": 0}

    def fake_get(*a, **k):
        calls["n"] += 1
        return _FakeResponse(FAKE_PAYLOAD)

    monkeypatch.setattr(odds_api.requests, "get", fake_get)

    odds_api.fetch_moneyline_odds()
    odds_api.fetch_moneyline_odds()  # debe venir del caché, no de una segunda llamada real

    assert calls["n"] == 1


def test_fetch_moneyline_odds_returns_empty_when_budget_exhausted_and_no_prior_cache(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "fake-key-for-tests")
    monkeypatch.setattr(odds_api, "ODDS_API_MONTHLY_BUDGET", 0)  # ya sin presupuesto desde el inicio

    def fail_if_called(*a, **k):
        raise AssertionError("no debería intentar una llamada real sin presupuesto")

    monkeypatch.setattr(odds_api.requests, "get", fail_if_called)

    assert odds_api.fetch_moneyline_odds() == []


def test_budget_guard_falls_back_to_stale_cache_when_exhausted(monkeypatch, tmp_path):
    monkeypatch.setenv("ODDS_API_KEY", "fake-key-for-tests")
    monkeypatch.setattr(odds_api, "ODDS_API_MONTHLY_BUDGET", 1)
    monkeypatch.setattr(odds_api.requests, "get", lambda *a, **k: _FakeResponse(FAKE_PAYLOAD))

    odds_api.fetch_moneyline_odds()  # consume el único request del presupuesto y escribe caché

    # Fuerza que el caché se lea como vencido (TTL=0) para simular que pasó
    # el tiempo, pero el presupuesto sigue agotado este mes.
    monkeypatch.setattr(odds_api, "ODDS_API_CACHE_TTL_SECONDS", 0)

    def fail_if_called(*a, **k):
        raise AssertionError("no debería intentar una llamada real sin presupuesto")

    monkeypatch.setattr(odds_api.requests, "get", fail_if_called)

    events = odds_api.fetch_moneyline_odds()
    # Se degrada al caché vencido en vez de quedarse sin nada.
    assert len(events) == 1
