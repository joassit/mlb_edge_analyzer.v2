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


def test_fetch_moneyline_odds_drops_malformed_price_but_keeps_the_rest(monkeypatch):
    import copy
    broken = copy.deepcopy(FAKE_PAYLOAD)
    # draftkings trae un precio imposible (fuera de rango) -- debe descartarse
    # esa cuota puntual sin tumbar el resto del evento ni al otro bookmaker.
    broken[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 999999

    monkeypatch.setenv("ODDS_API_KEY", "fake-key-for-tests")
    monkeypatch.setattr(odds_api.requests, "get", lambda *a, **k: _FakeResponse(broken))

    events = odds_api.fetch_moneyline_odds()

    assert len(events) == 1
    assert len(events[0]["prices"]) == 1  # solo fanduel sobrevive
    assert events[0]["prices"][0]["book"] == "fanduel"


def test_fetch_moneyline_odds_tags_old_quotes_as_not_fresh(monkeypatch):
    import copy
    from datetime import datetime, timezone
    old = copy.deepcopy(FAKE_PAYLOAD)
    old[0]["bookmakers"][0]["markets"][0]["last_update"] = "2020-01-01T00:00:00Z"
    old[0]["bookmakers"][1]["markets"][0]["last_update"] = datetime.now(timezone.utc).isoformat()

    monkeypatch.setenv("ODDS_API_KEY", "fake-key-for-tests")
    monkeypatch.setattr(odds_api.requests, "get", lambda *a, **k: _FakeResponse(old))

    events = odds_api.fetch_moneyline_odds()
    prices_by_book = {p["book"]: p for p in events[0]["prices"]}

    assert prices_by_book["draftkings"]["fresh"] is False
    assert prices_by_book["fanduel"]["fresh"] is True


def test_fetch_moneyline_odds_does_not_consume_budget_on_failed_request(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "fake-key-for-tests")
    monkeypatch.setattr(odds_api, "ODDS_API_MONTHLY_BUDGET", 5)

    import requests as real_requests

    def fail(*a, **k):
        raise real_requests.ConnectionError("red caída")

    monkeypatch.setattr(odds_api.requests, "get", fail)

    events = odds_api.fetch_moneyline_odds()
    assert events == []

    # La llamada fallida NO debe haber consumido presupuesto -- si lo
    # hubiera hecho, el contador del mes actual sería 1, no 0.
    from datetime import date
    month_key = date.today().strftime("%Y-%m")
    assert odds_api._read_budget_counts().get(month_key, 0) == 0


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


def test_match_odds_to_game_disambiguates_doubleheader_by_commence_time():
    # Doubleheader: mismos dos equipos, dos juegos el mismo día -- sin
    # desambiguar por hora, se le asignarían al juego 2 las cuotas del
    # juego 1 (pitchers y línea distintos).
    events = [
        {"away_team": "New York Mets", "home_team": "Atlanta Braves",
         "commence_time": "2026-07-05T17:05:00Z", "prices": [], "game": 1},
        {"away_team": "New York Mets", "home_team": "Atlanta Braves",
         "commence_time": "2026-07-05T21:35:00Z", "prices": [], "game": 2},
    ]

    game1 = odds_api.match_odds_to_game(events, "New York Mets", "Atlanta Braves",
                                         game_datetime_iso="2026-07-05T17:05:00Z")
    game2 = odds_api.match_odds_to_game(events, "New York Mets", "Atlanta Braves",
                                         game_datetime_iso="2026-07-05T21:40:00Z")

    assert game1["game"] == 1
    assert game2["game"] == 2


def test_match_odds_to_game_without_datetime_keeps_backward_compatible_first_match():
    events = [
        {"away_team": "New York Mets", "home_team": "Atlanta Braves",
         "commence_time": "2026-07-05T17:05:00Z", "prices": [], "game": 1},
        {"away_team": "New York Mets", "home_team": "Atlanta Braves",
         "commence_time": "2026-07-05T21:35:00Z", "prices": [], "game": 2},
    ]
    match = odds_api.match_odds_to_game(events, "New York Mets", "Atlanta Braves")
    assert match["game"] == 1


def test_match_odds_to_game_single_match_ignores_datetime():
    events = [{"away_team": "Boston Red Sox", "home_team": "New York Yankees",
               "commence_time": "2026-07-05T23:05:00Z", "prices": []}]
    match = odds_api.match_odds_to_game(events, "Boston Red Sox", "New York Yankees",
                                         game_datetime_iso="2026-07-06T01:00:00Z")
    assert match is not None


def test_best_available_price_excludes_stale_quotes():
    event = {
        "prices": [
            {"book": "draftkings", "away_price": 130, "home_price": -150, "fresh": False},
            {"book": "fanduel", "away_price": 125, "home_price": -145, "fresh": True},
        ]
    }
    best = odds_api.best_available_price(event)
    # Solo fanduel (fresh) cuenta -- si se colara draftkings, "away" sería 130
    assert best == {"away": 125, "home": -145}


def test_best_available_price_returns_none_when_all_stale():
    event = {"prices": [{"book": "draftkings", "away_price": 130, "home_price": -150, "fresh": False}]}
    assert odds_api.best_available_price(event) is None


def test_consensus_no_vig_prob_excludes_stale_quotes():
    event = {
        "prices": [
            {"book": "draftkings", "away_price": 130, "home_price": -150, "fresh": False},
            {"book": "fanduel", "away_price": 125, "home_price": -145, "fresh": True},
        ]
    }
    away_p, home_p = odds_api.consensus_no_vig_prob(event)
    # Con un solo book fresco, el consenso debe ser exactamente el no-vig de fanduel
    from model.edge import no_vig_probs
    expected_away, expected_home = no_vig_probs(125, -145)
    assert abs(away_p - expected_away) < 1e-9
    assert abs(home_p - expected_home) < 1e-9


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


# --- C3: no filtrar ODDS_API_KEY a los logs ---
# requests.RequestException incluye la URL completa (con apiKey=<valor
# real> en el query string) en su propio str() -- esos logs se suben como
# artifact de GitHub Actions (retención 30 días, repo público).

def test_sanitize_masks_api_key_in_url():
    text = ".../odds?apiKey=SECRETO123&regions=us"
    sanitized = odds_api._sanitize(text)
    assert "SECRETO123" not in sanitized
    assert "apiKey=***" in sanitized


def test_sanitize_preserves_host_and_status_code():
    text = ("HTTPSConnectionPool(host='api.the-odds-api.com', port=443): Max retries exceeded "
            "with url: /v4/sports/baseball_mlb/odds?apiKey=SECRETO123 (403 Forbidden)")
    sanitized = odds_api._sanitize(text)
    assert "api.the-odds-api.com" in sanitized
    assert "403" in sanitized
    assert "SECRETO123" not in sanitized


def test_sanitize_masks_token_param_case_insensitive():
    sanitized = odds_api._sanitize("...?Token=abc123&other=1")
    assert "abc123" not in sanitized


def test_sanitize_handles_exception_objects_directly():
    exc = ValueError("fallo en .../odds?apiKey=SECRETO123")
    sanitized = odds_api._sanitize(exc)
    assert "SECRETO123" not in sanitized


def test_fetch_moneyline_odds_sanitizes_api_key_from_error_log(monkeypatch, caplog):
    import logging
    import requests as real_requests

    monkeypatch.setenv("ODDS_API_KEY", "SECRETO123")
    monkeypatch.setattr(odds_api, "ODDS_API_MONTHLY_BUDGET", 5)

    def fail(*a, **k):
        raise real_requests.ConnectionError(
            "HTTPSConnectionPool(host='api.the-odds-api.com', port=443): Max retries exceeded with url: "
            "/v4/sports/baseball_mlb/odds?apiKey=SECRETO123&regions=us (403 Forbidden)"
        )

    monkeypatch.setattr(odds_api.requests, "get", fail)

    with caplog.at_level(logging.WARNING, logger="mlb_edge_analyzer"):
        events = odds_api.fetch_moneyline_odds()

    assert events == []
    log_text = " ".join(r.message for r in caplog.records)
    assert "SECRETO123" not in log_text
    assert "api.the-odds-api.com" in log_text
    assert "403" in log_text
