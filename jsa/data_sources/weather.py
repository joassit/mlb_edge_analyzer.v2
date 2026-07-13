"""Clima del estadio via Open-Meteo (https://open-meteo.com) -- gratis, sin
API key. Solo tiene sentido para juegos de hoy/proximos dias (pronostico,
no clima historico)."""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("jsa")

OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"

_session = requests.Session()
_session.mount(
    "https://", HTTPAdapter(max_retries=Retry(total=1, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504]))
)


def get_game_weather(lat: float | None, lon: float | None, game_datetime_iso: str | None) -> dict:
    """Temperatura (F) y velocidad de viento (mph) para la hora del
    pronostico mas cercana al horario del juego. `game_datetime_iso`: string
    ISO 8601 (ej. '2026-07-02T23:05:00Z')."""
    result: dict = {"temp_f": None, "wind_mph": None}

    if lat is None or lon is None or not game_datetime_iso:
        return result

    try:
        game_dt = datetime.fromisoformat(game_datetime_iso.replace("Z", "+00:00"))
    except ValueError:
        return result

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,wind_speed_10m",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "UTC",
        "start_date": game_dt.strftime("%Y-%m-%d"),
        "end_date": game_dt.strftime("%Y-%m-%d"),
    }

    try:
        resp = _session.get(OPEN_METEO_BASE, params=params, timeout=6)
        resp.raise_for_status()
        data = resp.json()

        times = data["hourly"]["time"]
        temps = data["hourly"]["temperature_2m"]
        winds = data["hourly"]["wind_speed_10m"]

        target_hour = game_dt.strftime("%Y-%m-%dT%H:00")
        if target_hour in times:
            idx = times.index(target_hour)
        else:
            # Hora disponible mas cercana en el tiempo, no la primera del
            # dia -- lecciones de mlb_edge_analyzer.v2/data/weather.py.
            game_dt_naive = game_dt.replace(tzinfo=None)
            hour_dts = [datetime.fromisoformat(t) for t in times]
            idx = min(range(len(hour_dts)), key=lambda i: abs((hour_dts[i] - game_dt_naive).total_seconds()))

        result["temp_f"] = temps[idx]
        result["wind_mph"] = winds[idx]
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.warning("No se pudo obtener clima (%s,%s): %s", lat, lon, e)

    return result


def preload_weather(games: list[dict], park_lookup) -> dict[int, dict]:
    """Trae el clima de todos los estadios de la jornada EN PARALELO, una
    sola vez por equipo local. `park_lookup`: funcion tipo
    `park_factors.get_park_info`. Devuelve {home_team_id: {...}}."""
    unique_by_team: dict[int, tuple] = {}
    for g in games:
        team_id = g["home_team_id"]
        if team_id not in unique_by_team:
            park = park_lookup(team_id)
            unique_by_team[team_id] = (park["lat"], park["lon"], g.get("game_time"))

    results: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_team = {
            executor.submit(get_game_weather, lat, lon, game_time): team_id
            for team_id, (lat, lon, game_time) in unique_by_team.items()
        }
        for future in future_to_team:
            team_id = future_to_team[future]
            try:
                results[team_id] = future.result()
            except Exception as e:  # noqa: BLE001 -- aislar fallas por equipo, nunca tumbar el lote
                logger.warning("Clima fallo para team_id=%s: %s", team_id, e)
                results[team_id] = {"temp_f": None, "wind_mph": None}
    return results
