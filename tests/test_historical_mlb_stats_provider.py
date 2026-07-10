"""
Pruebas de MLBStatsAPIProvider (historical_engine/point_in_time_provider.py)
contra HTTP mockeado (cero red real) -- a diferencia de
tests/test_historical_point_in_time.py, que mockea el provider entero vía
FakeProvider, acá se verifica el nivel de detalle de QUÉ parámetros se le
mandan a la API real, para casos donde ese detalle es la corrección en sí
(ver bullpen_era_as_of: el fix de la auditoría de look-ahead bias fue
agregar `date=` a la llamada de roster -- un test que solo mockeara el
provider entero nunca hubiera detectado una regresión ahí).
"""

import historical_engine.point_in_time_provider as pitp


class _FakeResponse:
    def __init__(self, json_data):
        self._json = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def test_bullpen_era_as_of_requests_roster_with_historical_date_cutoff(monkeypatch):
    """El roster debe pedirse con date=as_of_date-1 (mismo corte que
    _end_date usa para todo lo demás) -- nunca con la fecha de hoy, y
    nunca solo `season` sin fecha (eso devolvía el roster ACTUAL, el bug
    que encontró la auditoría de look-ahead bias)."""
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        if url.endswith("/roster"):
            return _FakeResponse({"roster": [{"person": {"id": 1}, "position": {"abbreviation": "P"}}]})
        # pitcher_era_ip_as_of para el único pitcher del roster fake
        return _FakeResponse({"stats": [{"splits": [{"stat": {"era": "3.00", "inningsPitched": "10.0"}}]}]})

    monkeypatch.setattr(pitp, "session", type("S", (), {"get": staticmethod(fake_get)})())

    provider = pitp.MLBStatsAPIProvider()
    provider.bullpen_era_as_of(team_id=147, as_of_date="2024-07-16", season=2024)

    roster_calls = [c for c in calls if c[0].endswith("/roster")]
    assert len(roster_calls) == 1
    _, params = roster_calls[0]
    assert params["rosterType"] == "active"
    assert params["date"] == "2024-07-15"  # as_of_date - 1 dia, igual que _end_date()
    assert "season" not in params  # el bug original: pedía season sin fecha -> roster de HOY


def test_bullpen_era_as_of_computes_weighted_era_from_roster_pitchers(monkeypatch):
    """Confirma que el cambio de parámetro no rompió el cálculo -- sigue
    ponderando ERA por innings pitched de los pitchers devueltos."""
    roster = {"roster": [
        {"person": {"id": 1}, "position": {"abbreviation": "P"}},
        {"person": {"id": 2}, "position": {"abbreviation": "P"}},
        {"person": {"id": 3}, "position": {"abbreviation": "1B"}},  # no-pitcher, debe ignorarse
    ]}
    stats_by_pid = {
        1: {"era": "3.00", "inningsPitched": "10.0"},
        2: {"era": "6.00", "inningsPitched": "5.0"},
    }

    def fake_get(url, params=None, timeout=None):
        if url.endswith("/roster"):
            return _FakeResponse(roster)
        pid = int(url.rsplit("/", 2)[-2])
        return _FakeResponse({"stats": [{"splits": [{"stat": stats_by_pid[pid]}]}]})

    monkeypatch.setattr(pitp, "session", type("S", (), {"get": staticmethod(fake_get)})())

    provider = pitp.MLBStatsAPIProvider()
    era = provider.bullpen_era_as_of(team_id=147, as_of_date="2024-07-16", season=2024)

    # (3.00*10 + 6.00*5) / 15 = 4.00
    assert era == 4.0
