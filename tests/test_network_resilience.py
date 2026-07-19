"""
V4 — las funciones de ingesta (data/stats.py, data/mlb_api.py) deben
degradar con gracia (None/[]/fallback) ante fallos de red, no propagar la
excepción y tumbar el pipeline completo. Antes de esta corrección, solo
get_bullpen_era() y get_pitcher_command()/get_pitcher_rest() lo hacían --
get_pitcher_era_ip(), get_team_ops(), get_league_ops(), get_schedule() y
get_game_result() dejaban que requests.RequestException/HTTPError se
propagara sin capturar.
"""

import requests

import data.mlb_api as mlb_api_mod
import data.stats as stats_mod
from data.mlb_api import get_game_result, get_schedule, find_makeup_game_result
from data.stats import get_league_ops, get_pitcher_era_ip, get_team_ops


def _raise(exc):
    def _fn(*args, **kwargs):
        raise exc
    return _fn


def test_get_pitcher_era_ip_returns_none_on_timeout(monkeypatch):
    # M3: get_pitcher_era() (código muerto, sin ningún caller real) se
    # eliminó -- este test, migrado de esa función, cubre el mismo tipo de
    # excepción (Timeout) para get_pitcher_era_ip(), que sí está conectada
    # al pipeline real.
    monkeypatch.setattr(stats_mod, "_pitcher_stats_cache", {})
    monkeypatch.setattr(stats_mod.session, "get", _raise(requests.Timeout()))
    assert get_pitcher_era_ip(999001) is None


def test_get_pitcher_era_ip_returns_none_on_connection_error(monkeypatch):
    monkeypatch.setattr(stats_mod, "_pitcher_stats_cache", {})
    monkeypatch.setattr(stats_mod.session, "get", _raise(requests.ConnectionError()))
    assert get_pitcher_era_ip(999002) is None


def test_get_team_ops_returns_none_on_http_error(monkeypatch):
    class _FakeResp:
        def raise_for_status(self):
            raise requests.HTTPError("500 Server Error")

    monkeypatch.setattr(stats_mod.session, "get", lambda *a, **k: _FakeResp())
    assert get_team_ops(999003) is None


def test_get_league_ops_falls_back_to_default_on_network_error(monkeypatch):
    monkeypatch.setattr(stats_mod, "_league_ops_cache", None)
    monkeypatch.setattr(stats_mod.session, "get", _raise(requests.ConnectionError()))
    assert get_league_ops() == 0.750


def test_get_schedule_returns_empty_list_on_timeout(monkeypatch):
    monkeypatch.setattr(mlb_api_mod.session, "get", _raise(requests.Timeout()))
    assert get_schedule() == []


def test_get_game_result_returns_none_on_connection_error(monkeypatch):
    monkeypatch.setattr(mlb_api_mod.session, "get", _raise(requests.ConnectionError()))
    assert get_game_result(717468) is None


def test_get_schedule_returns_empty_list_on_malformed_json(monkeypatch):
    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr(mlb_api_mod.session, "get", lambda *a, **k: _FakeResp())
    assert get_schedule() == []


def test_get_game_result_logs_warning_when_final_but_postponed_with_no_linescore(monkeypatch, caplog):
    # Encontrado auditando 2 filas huérfanas reales (informe técnico del
    # 2026-07-11): abstractGameState=Final pero detailedState=Postponed y
    # linescore={} -- el juego nunca completó un marcador bajo este game_pk.
    # Sigue devolviendo None (mismo contrato, el caller reintenta), pero
    # ahora queda en el log en vez de parecer "todavía no jugado".
    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "dates": [{
                    "games": [{
                        "status": {"abstractGameState": "Final", "detailedState": "Postponed"},
                        "linescore": {"teams": {}},
                    }]
                }]
            }

    monkeypatch.setattr(mlb_api_mod.session, "get", lambda *a, **k: _FakeResp())
    with caplog.at_level("WARNING"):
        result = get_game_result(823062)

    assert result is None
    assert any("game_pk=823062" in r.message and "Postponed" in r.message for r in caplog.records)


class _FakeJsonResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _postponed_status_payload(reschedule_game_date="2026-07-11"):
    """Respuesta de schedule?gamePk=X para un juego pospuesto -- mismo
    shape real observado en la investigación de las 4 filas huérfanas de
    los informes técnicos del 2026-07-07 al 07-18."""
    return {
        "dates": [{
            "games": [{
                "status": {"abstractGameState": "Final", "detailedState": "Postponed"},
                "rescheduleGameDate": reschedule_game_date,
                "linescore": {"teams": {}},
            }]
        }]
    }


def test_find_makeup_game_result_returns_none_when_original_not_postponed(monkeypatch):
    # Si el game_pk original no está en estado Postponed (p.ej. sigue
    # genuinamente en curso), no tiene sentido gastar la llamada extra de
    # búsqueda -- se corta después de la primera consulta.
    calls = []

    def _fake_get(url, params=None, timeout=None):
        calls.append(params)
        return _FakeJsonResp({
            "dates": [{"games": [{"status": {"abstractGameState": "Live", "detailedState": "In Progress"}}]}]
        })

    monkeypatch.setattr(mlb_api_mod.session, "get", _fake_get)

    result = find_makeup_game_result(823357, "Milwaukee Brewers", "Pittsburgh Pirates", "2026-07-10")

    assert result is None
    assert len(calls) == 1


def test_find_makeup_game_result_finds_real_game_under_different_game_pk(monkeypatch):
    # Caso real confirmado: game_pk=823357 (Brewers @ Pirates, 2026-07-10)
    # pospuesto por clima -- el juego real se jugó como game_pk=823356 el
    # 07-11 (final 3-2).
    responses = [
        _postponed_status_payload(reschedule_game_date="2026-07-11"),
        {
            "dates": [{
                "games": [
                    {
                        "gamePk": 823356,
                        "teams": {
                            "away": {"team": {"name": "Milwaukee Brewers"}},
                            "home": {"team": {"name": "Pittsburgh Pirates"}},
                        },
                        "status": {"abstractGameState": "Final"},
                        "linescore": {"teams": {"home": {"runs": 3}, "away": {"runs": 2}}},
                    },
                    {
                        # Otro partido cualquiera en la ventana -- no debe confundirse
                        "gamePk": 900001,
                        "teams": {
                            "away": {"team": {"name": "Some Team"}},
                            "home": {"team": {"name": "Other Team"}},
                        },
                        "status": {"abstractGameState": "Final"},
                        "linescore": {"teams": {"home": {"runs": 1}, "away": {"runs": 0}}},
                    },
                ]
            }]
        },
    ]
    calls = []

    def _fake_get(url, params=None, timeout=None):
        calls.append(params)
        return _FakeJsonResp(responses[len(calls) - 1])

    monkeypatch.setattr(mlb_api_mod.session, "get", _fake_get)

    result = find_makeup_game_result(823357, "Milwaukee Brewers", "Pittsburgh Pirates", "2026-07-10")

    assert result == {
        "home_score": 3, "away_score": 2, "winner": "home", "total_runs": 5,
        "resolved_via_game_pk": 823356,
    }
    assert len(calls) == 2


def test_find_makeup_game_result_returns_none_when_no_matching_makeup_in_window(monkeypatch):
    responses = [
        _postponed_status_payload(reschedule_game_date="2026-07-11"),
        {"dates": [{"games": []}]},
    ]
    calls = []

    def _fake_get(url, params=None, timeout=None):
        calls.append(params)
        return _FakeJsonResp(responses[len(calls) - 1])

    monkeypatch.setattr(mlb_api_mod.session, "get", _fake_get)

    result = find_makeup_game_result(823357, "Milwaukee Brewers", "Pittsburgh Pirates", "2026-07-10")

    assert result is None
