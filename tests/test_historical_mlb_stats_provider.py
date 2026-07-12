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


def test_historical_weather_never_queries_a_window_overlapping_as_of_date_or_later(monkeypatch):
    """Regresión del hallazgo de auditoría: historical_weather() usaba
    game_date directamente (el clima REAL del propio partido), la única
    variable del proveedor que rompía la invariante point-in-time. Ahora
    usa climatología de años anteriores -- ninguna ventana consultada
    puede llegar a as_of_date ni a game_date."""
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        return pitp_FakeResponse({"hourly": {"temperature_2m": [70.0, 72.0, 74.0]}})

    def pitp_FakeResponse(data):
        return type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: data})()

    monkeypatch.setattr(pitp, "session", type("S", (), {"get": staticmethod(fake_get)})())

    provider = pitp.MLBStatsAPIProvider()
    result = provider.historical_weather(lat=40.0, lon=-74.0, game_date="2024-07-16", as_of_date="2024-07-15")

    assert calls, "no se hizo ninguna llamada de climatología"
    as_of = pitp.date.fromisoformat("2024-07-15")
    for params in calls:
        window_end = pitp.date.fromisoformat(params["end_date"])
        window_start = pitp.date.fromisoformat(params["start_date"])
        assert window_end < as_of, f"ventana {params} llega hasta as_of_date o después"
        assert window_start.year < as_of.year  # cada ventana vive en un año calendario anterior completo
    assert result["temp_f"] == 72.0  # promedio simple de todas las llamadas mockeadas (todas iguales acá)


def test_historical_weather_averages_across_climatology_years(monkeypatch):
    """Confirma que sí promedia sobre múltiples años (no solo el más
    reciente) -- valores bien distintos por año deben reflejarse en el
    promedio final, no en el valor de un solo año. WEATHER_CLIMATOLOGY_YEARS=3
    (bajado de 5 -- ver commit de optimización de costo tras el timeout de
    la corrida de 2025): solo se consultan los 3 años inmediatamente
    anteriores al de as_of_date."""
    year_temps = {2019: [10.0], 2020: [20.0], 2021: [70.0], 2022: [80.0], 2023: [90.0]}

    def fake_get(url, params=None, timeout=None):
        yr = int(params["start_date"][:4])
        return type("R", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"hourly": {"temperature_2m": year_temps[yr]}},
        })()

    monkeypatch.setattr(pitp, "session", type("S", (), {"get": staticmethod(fake_get)})())

    provider = pitp.MLBStatsAPIProvider()
    result = provider.historical_weather(lat=40.0, lon=-74.0, game_date="2024-07-16", as_of_date="2024-07-15")

    # Solo 2021/2022/2023 (los 3 años anteriores a 2024) -- si tocara
    # 2019/2020 el promedio bajaría mucho (10.0/20.0 están ahí a propósito
    # para que este test falle fuerte si alguien vuelve a subir el rango).
    assert result["temp_f"] == sum([70, 80, 90]) / 3  # 80.0


def test_historical_weather_caches_within_the_same_provider_instance(monkeypatch):
    """Dos juegos de un doble cartelera (mismo parque, mismo game_date,
    mismo as_of_date) no deben repetir las llamadas de climatología -- la
    segunda consulta debe pegar en cache. Ahorra costo real: fue parte de
    la optimización tras el timeout de la corrida de 2025."""
    call_count = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        call_count["n"] += 1
        return type("R", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"hourly": {"temperature_2m": [75.0]}},
        })()

    monkeypatch.setattr(pitp, "session", type("S", (), {"get": staticmethod(fake_get)})())

    provider = pitp.MLBStatsAPIProvider()
    r1 = provider.historical_weather(lat=40.0, lon=-74.0, game_date="2024-07-16", as_of_date="2024-07-15")
    calls_after_first = call_count["n"]
    r2 = provider.historical_weather(lat=40.0, lon=-74.0, game_date="2024-07-16", as_of_date="2024-07-15")

    assert calls_after_first == 3  # WEATHER_CLIMATOLOGY_YEARS llamadas reales
    assert call_count["n"] == calls_after_first  # la segunda vez, cero llamadas nuevas
    assert r1 == r2 == {"temp_f": 75.0}
