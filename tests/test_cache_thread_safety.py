"""
V7 — las cachés globales de data/stats.py (_pitcher_stats_cache,
_league_ops_cache, _bullpen_cache) comparten un único _cache_lock. Sin él,
dos threads que llaman get_league_ops() (u otra función cacheada) al mismo
tiempo pueden leer "caché vacía" ambos, disparar dos llamadas a la API en
paralelo y pisarse la escritura -- el GIL evita un crash, pero el
resultado queda no-determinístico.
"""

import threading

import data.stats as stats


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_cache_lock_exists_and_is_a_real_lock():
    assert hasattr(stats, "_cache_lock")
    assert isinstance(stats._cache_lock, type(threading.Lock()))


def test_league_ops_cache_thread_safe(monkeypatch):
    payload = {"stats": [{"splits": [{"stat": {"plateAppearances": 600, "ops": 0.800}}]}]}
    monkeypatch.setattr(stats.session, "get", lambda *a, **k: _FakeResponse(payload))
    monkeypatch.setattr(stats, "_league_ops_cache", None)

    results = []

    def fetch():
        results.append(stats.get_league_ops(season=2026))

    threads = [threading.Thread(target=fetch) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    assert len(set(results)) == 1  # todos los threads deben ver el mismo valor cacheado


def test_pitcher_stats_cache_thread_safe(monkeypatch):
    payload = {"stats": [{"splits": [{"stat": {"era": "3.10", "inningsPitched": "150.0"}}]}]}
    monkeypatch.setattr(stats.session, "get", lambda *a, **k: _FakeResponse(payload))
    monkeypatch.setattr(stats, "_pitcher_stats_cache", {})

    results = []

    def fetch():
        results.append(stats.get_pitcher_era_ip(pitcher_id=555, season=2026))

    threads = [threading.Thread(target=fetch) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    assert all(r == (3.10, 150.0) for r in results)
