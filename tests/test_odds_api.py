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
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

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


def test_consensus_power_devig_prob_averages_across_books():
    event = {
        "prices": [
            {"book": "draftkings", "away_price": 130, "home_price": -150},
            {"book": "fanduel", "away_price": 125, "home_price": -145},
        ]
    }
    away_p, home_p = odds_api.consensus_power_devig_prob(event)
    assert abs((away_p + home_p) - 1.0) < 1e-9
    assert 0 < away_p < home_p < 1


def test_consensus_power_devig_prob_none_without_prices():
    assert odds_api.consensus_power_devig_prob({"prices": []}) is None


def test_consensus_power_devig_prob_differs_from_proportional_consensus():
    # Si diera lo mismo que consensus_no_vig_prob(), esta prueba (y la
    # función misma) no protegerían nada -- confirma que de verdad usa
    # power_devig, no no_vig_probs por error de copiar/pegar.
    event = {"prices": [{"book": "fakebook", "away_price": -250, "home_price": 210}]}
    away_power, _ = odds_api.consensus_power_devig_prob(event)
    away_prop, _ = odds_api.consensus_no_vig_prob(event)
    assert abs(away_power - away_prop) > 1e-6


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


# --- Múltiples API keys con rotación automática ---
# ODDS_API_KEYS (separadas por coma) permite configurar varias keys; si
# la que está en uso agota su presupuesto local, o responde 401/429, o
# falla por red, se prueba la siguiente antes de degradarse al caché.

def test_resolve_odds_api_keys_falls_back_to_single_key_when_odds_api_keys_unset(monkeypatch):
    import config
    monkeypatch.delenv("ODDS_API_KEYS", raising=False)
    monkeypatch.setenv("ODDS_API_KEY", "sola-key")
    assert config.resolve_odds_api_keys() == ["sola-key"]


def test_resolve_odds_api_keys_splits_comma_separated_list(monkeypatch):
    import config
    monkeypatch.setenv("ODDS_API_KEYS", "key1, key2 ,key3")
    assert config.resolve_odds_api_keys() == ["key1", "key2", "key3"]


def test_resolve_odds_api_keys_returns_empty_list_without_any_env_var(monkeypatch):
    import config
    monkeypatch.delenv("ODDS_API_KEYS", raising=False)
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    assert config.resolve_odds_api_keys() == []


def test_fetch_moneyline_odds_skips_key_whose_local_budget_is_exhausted(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setenv("ODDS_API_KEYS", "key-uno,key-dos")
    monkeypatch.setattr(odds_api, "ODDS_API_MONTHLY_BUDGET", 1)

    # key-uno ya agotó su presupuesto local (1/1) -- key-dos todavía no.
    odds_api._record_budget_usage("key-uno")

    calls = {"keys_used": []}

    def fake_get(url, params=None, **kwargs):
        calls["keys_used"].append(params["apiKey"])
        return _FakeResponse(FAKE_PAYLOAD)

    monkeypatch.setattr(odds_api.requests, "get", fake_get)

    events = odds_api.fetch_moneyline_odds()

    assert len(events) == 1
    # Nunca se intentó la llamada HTTP con key-uno -- se saltó directo a key-dos.
    assert calls["keys_used"] == ["key-dos"]


def test_fetch_moneyline_odds_rotates_to_next_key_on_401(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setenv("ODDS_API_KEYS", "key-invalida,key-buena")

    def fake_get(url, params=None, **kwargs):
        if params["apiKey"] == "key-invalida":
            return _FakeResponse(None, status_code=401)
        return _FakeResponse(FAKE_PAYLOAD)

    monkeypatch.setattr(odds_api.requests, "get", fake_get)

    events = odds_api.fetch_moneyline_odds()

    assert len(events) == 1
    assert events[0]["away_team"] == "Boston Red Sox"


def test_fetch_moneyline_odds_rotates_to_next_key_on_429(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setenv("ODDS_API_KEYS", "key-limitada,key-buena")

    def fake_get(url, params=None, **kwargs):
        if params["apiKey"] == "key-limitada":
            return _FakeResponse(None, status_code=429)
        return _FakeResponse(FAKE_PAYLOAD)

    monkeypatch.setattr(odds_api.requests, "get", fake_get)

    events = odds_api.fetch_moneyline_odds()
    assert len(events) == 1


def test_fetch_moneyline_odds_rotation_never_logs_any_key_in_plaintext(monkeypatch, caplog):
    import logging
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setenv("ODDS_API_KEYS", "SECRETO-UNO,SECRETO-DOS")

    def fake_get(url, params=None, **kwargs):
        if params["apiKey"] == "SECRETO-UNO":
            return _FakeResponse(None, status_code=401)
        return _FakeResponse(FAKE_PAYLOAD)

    monkeypatch.setattr(odds_api.requests, "get", fake_get)

    with caplog.at_level(logging.WARNING, logger="mlb_edge_analyzer"):
        events = odds_api.fetch_moneyline_odds()

    assert len(events) == 1
    log_text = " ".join(r.message for r in caplog.records)
    assert "SECRETO-UNO" not in log_text
    assert "SECRETO-DOS" not in log_text


def test_fetch_moneyline_odds_stops_at_first_working_key_without_trying_the_rest(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setenv("ODDS_API_KEYS", "key-buena,key-nunca-deberia-intentarse")

    calls = {"n": 0}

    def fake_get(url, params=None, **kwargs):
        calls["n"] += 1
        if params["apiKey"] == "key-nunca-deberia-intentarse":
            raise AssertionError("no debería probar la segunda key si la primera funcionó")
        return _FakeResponse(FAKE_PAYLOAD)

    monkeypatch.setattr(odds_api.requests, "get", fake_get)

    odds_api.fetch_moneyline_odds()
    assert calls["n"] == 1


def test_fetch_moneyline_odds_falls_back_to_stale_cache_when_all_keys_fail(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setenv("ODDS_API_KEYS", "key-uno,key-dos")
    monkeypatch.setattr(odds_api.requests, "get", lambda *a, **k: _FakeResponse(FAKE_PAYLOAD))

    odds_api.fetch_moneyline_odds()  # escribe caché con key-uno

    # Fuerza caché vencido; ambas keys fallan esta vez.
    monkeypatch.setattr(odds_api, "ODDS_API_CACHE_TTL_SECONDS", 0)

    def always_fail(url, params=None, **kwargs):
        return _FakeResponse(None, status_code=401)

    monkeypatch.setattr(odds_api.requests, "get", always_fail)

    events = odds_api.fetch_moneyline_odds()
    # Mismo comportamiento de hoy: se degrada al caché vencido en vez de [].
    assert len(events) == 1


def test_fetch_moneyline_odds_returns_empty_when_all_keys_fail_and_no_cache(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setenv("ODDS_API_KEYS", "key-uno,key-dos")
    monkeypatch.setattr(odds_api.requests, "get", lambda *a, **k: _FakeResponse(None, status_code=401))

    assert odds_api.fetch_moneyline_odds() == []


def test_budget_counters_do_not_mix_between_different_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(odds_api, "ODDS_CACHE_DIR", str(tmp_path))
    odds_api._record_budget_usage("key-uno")
    odds_api._record_budget_usage("key-uno")
    odds_api._record_budget_usage("key-dos")

    from datetime import date
    month_key = date.today().strftime("%Y-%m")
    counts = odds_api._read_budget_counts()[month_key]
    assert counts[odds_api._hash_key("key-uno")]["used"] == 2
    assert counts[odds_api._hash_key("key-dos")]["used"] == 1


def test_record_budget_usage_persists_provider_headers(monkeypatch, tmp_path):
    monkeypatch.setattr(odds_api, "ODDS_CACHE_DIR", str(tmp_path))
    odds_api._record_budget_usage("key-uno", provider_used=15, provider_remaining=485)

    from datetime import date
    month_key = date.today().strftime("%Y-%m")
    entry = odds_api._read_budget_counts()[month_key][odds_api._hash_key("key-uno")]
    assert entry["provider_used"] == 15
    assert entry["provider_remaining"] == 485


def test_record_budget_usage_reads_remaining_from_response_headers(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setenv("ODDS_API_KEYS", "key-uno")
    monkeypatch.setattr(
        odds_api.requests, "get",
        lambda *a, **k: _FakeResponse(FAKE_PAYLOAD, headers={"x-requests-remaining": "485", "x-requests-used": "15"}),
    )

    odds_api.fetch_moneyline_odds()

    from datetime import date
    month_key = date.today().strftime("%Y-%m")
    entry = odds_api._read_budget_counts()[month_key][odds_api._hash_key("key-uno")]
    assert entry["provider_remaining"] == 485
    assert entry["provider_used"] == 15


def test_record_budget_usage_warns_when_provider_reports_low_remaining(monkeypatch, caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="mlb_edge_analyzer"):
        # 15 restantes de un total de 100 (15 + 85) = 15% -- por debajo del 20%.
        odds_api._record_budget_usage("key-uno", provider_used=85, provider_remaining=15)

    log_text = " ".join(r.message for r in caplog.records)
    assert "poco margen" in log_text.lower()


def test_sanitize_masks_regardless_of_which_key_was_used():
    # M4/rotación: _sanitize() debe enmascarar el valor real de apiKey sin
    # importar cuál de las N keys configuradas se haya usado en la URL.
    for key_value in ("SECRETO-UNO", "otra-key-distinta-456"):
        text = f".../odds?apiKey={key_value}&regions=us"
        sanitized = odds_api._sanitize(text)
        assert key_value not in sanitized
        assert "apiKey=***" in sanitized


# --- Auditabilidad de mercado: fuente/antigüedad de la cuota usada ---
# get_last_fetch_meta() expone de dónde salieron las cuotas de la última
# llamada, para que reports/generate_report.py pueda mostrar "fuente:
# API en vivo / caché / manual" en vez de inventar ese dato.

def test_get_last_fetch_meta_reports_none_without_api_key(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    odds_api.fetch_moneyline_odds()
    meta = odds_api.get_last_fetch_meta()
    assert meta["source"] == "none"
    assert meta["fetched_at"] is None


def test_get_last_fetch_meta_reports_api_live_after_fresh_call(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "fake-key-for-tests")
    monkeypatch.setattr(odds_api.requests, "get", lambda *a, **k: _FakeResponse(FAKE_PAYLOAD))

    odds_api.fetch_moneyline_odds()

    meta = odds_api.get_last_fetch_meta()
    assert meta["source"] == "api_live"
    assert meta["fetched_at"] is not None


def test_get_last_fetch_meta_reports_api_cache_when_served_from_cache(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "fake-key-for-tests")
    monkeypatch.setattr(odds_api.requests, "get", lambda *a, **k: _FakeResponse(FAKE_PAYLOAD))

    odds_api.fetch_moneyline_odds()  # primera llamada real, escribe caché
    odds_api.fetch_moneyline_odds()  # segunda: debe venir del caché

    meta = odds_api.get_last_fetch_meta()
    assert meta["source"] == "api_cache"
    assert meta["fetched_at"] is not None


def test_get_last_fetch_meta_reports_api_stale_cache_when_budget_exhausted(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "fake-key-for-tests")
    monkeypatch.setattr(odds_api, "ODDS_API_MONTHLY_BUDGET", 1)
    monkeypatch.setattr(odds_api.requests, "get", lambda *a, **k: _FakeResponse(FAKE_PAYLOAD))

    odds_api.fetch_moneyline_odds()  # consume el único request del presupuesto

    monkeypatch.setattr(odds_api, "ODDS_API_CACHE_TTL_SECONDS", 0)

    def fail_if_called(*a, **k):
        raise AssertionError("no debería intentar una llamada real sin presupuesto")

    monkeypatch.setattr(odds_api.requests, "get", fail_if_called)

    odds_api.fetch_moneyline_odds()

    meta = odds_api.get_last_fetch_meta()
    assert meta["source"] == "api_stale_cache"
    assert meta["fetched_at"] is not None


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


# --- M2: escritura atómica de caché/presupuesto (temp file + os.replace) ---
# open(path, "w") directo trunca el archivo ANTES de escribir nada -- un
# fallo a mitad de la escritura (crash, excepción) deja el archivo real
# vacío/corrupto. _atomic_write_json() escribe a un temporal y solo
# reemplaza el destino si la escritura completa tuvo éxito.

def test_atomic_write_json_writes_correct_content(tmp_path):
    path = str(tmp_path / "data.json")
    odds_api._atomic_write_json(path, {"a": 1, "b": [1, 2, 3]})

    import json
    with open(path) as f:
        assert json.load(f) == {"a": 1, "b": [1, 2, 3]}


def test_atomic_write_json_leaves_original_file_intact_on_mid_write_failure(tmp_path, monkeypatch):
    import json
    path = str(tmp_path / "cache.json")
    original = {"fetched_at": 123, "payload": ["datos", "reales", "importantes"]}
    with open(path, "w") as f:
        json.dump(original, f)

    def fail_dump(*a, **k):
        raise RuntimeError("simula un crash a mitad de la escritura")

    monkeypatch.setattr(odds_api.json, "dump", fail_dump)

    try:
        odds_api._atomic_write_json(path, {"fetched_at": 456, "payload": ["nuevo"]})
    except RuntimeError:
        pass

    with open(path) as f:
        assert json.load(f) == original  # el archivo real NUNCA se tocó


def test_atomic_write_json_does_not_leave_temp_file_behind_on_success(tmp_path):
    import os
    path = str(tmp_path / "cache.json")
    odds_api._atomic_write_json(path, {"x": 1})

    leftover = [f for f in os.listdir(tmp_path) if f != "cache.json"]
    assert leftover == []


def test_write_cache_uses_atomic_write(monkeypatch, tmp_path):
    monkeypatch.setattr(odds_api, "ODDS_CACHE_DIR", str(tmp_path))
    odds_api._write_cache([{"book": "draftkings"}])

    cached = odds_api._read_cache(ignore_ttl=True)
    assert cached == [{"book": "draftkings"}]


def test_record_budget_usage_uses_atomic_write(monkeypatch, tmp_path):
    # Firma cambió a _record_budget_usage(key, ...) -- el contador ahora es
    # por (mes, key), no un solo entero global (ver soporte de múltiples
    # API keys en fetch_moneyline_odds).
    monkeypatch.setattr(odds_api, "ODDS_CACHE_DIR", str(tmp_path))
    odds_api._record_budget_usage("fake-key")
    odds_api._record_budget_usage("fake-key")

    from datetime import date
    month_key = date.today().strftime("%Y-%m")
    key_hash = odds_api._hash_key("fake-key")
    assert odds_api._read_budget_counts()[month_key][key_hash]["used"] == 2
