"""
Pruebas de data/weather.py — get_game_weather() con la API de Open-Meteo
mockeada (nunca red real).
"""

import data.weather as weather


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_get_game_weather_falls_back_to_nearest_hour_not_first_of_day(monkeypatch):
    # B2: si la hora exacta del juego no está en el pronóstico (ej. Open-Meteo
    # no devuelve la última hora del día), debe usarse la hora disponible
    # más CERCANA en el tiempo, no la primera del día (índice 0).
    payload = {
        "hourly": {
            "time": [f"2026-07-02T{h:02d}:00" for h in range(0, 23)],  # falta la hora 23
            "temperature_2m": [60 + h for h in range(0, 23)],  # 60..82
            "wind_speed_10m": [5] * 23,
            "wind_direction_10m": [180] * 23,
        }
    }
    monkeypatch.setattr(weather._session, "get", lambda *a, **k: _FakeResponse(payload))

    result = weather.get_game_weather(40.0, -75.0, "2026-07-02T23:05:00Z")

    # La hora más cercana a las 23:05 es la 22:00 (temp 82), no la 00:00 (temp 60).
    assert result["temp_f"] == 82


def test_get_game_weather_uses_exact_match_when_available(monkeypatch):
    payload = {
        "hourly": {
            "time": [f"2026-07-02T{h:02d}:00" for h in range(0, 24)],
            "temperature_2m": [60 + h for h in range(0, 24)],
            "wind_speed_10m": [5 + h for h in range(0, 24)],
            "wind_direction_10m": [10 * h for h in range(0, 24)],
        }
    }
    monkeypatch.setattr(weather._session, "get", lambda *a, **k: _FakeResponse(payload))

    result = weather.get_game_weather(40.0, -75.0, "2026-07-02T15:05:00Z")

    assert result["temp_f"] == 75
    assert result["wind_mph"] == 20
    assert result["wind_direction_deg"] == 150


def test_get_game_weather_returns_none_fields_when_lat_lon_missing():
    result = weather.get_game_weather(None, None, "2026-07-02T15:05:00Z")
    assert result == {"temp_f": None, "wind_mph": None, "wind_direction_deg": None}
