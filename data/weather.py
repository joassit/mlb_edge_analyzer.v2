"""
Clima del estadio vía Open-Meteo (https://open-meteo.com) — gratis, sin API key.

El clima solo tiene sentido para juegos de HOY o los próximos días (Open-Meteo
da pronóstico, no clima de un juego pasado). Para juegos ya jugados, esto
no aplica.
"""

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("mlb_edge_analyzer")

OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"

# Sesión con reintentos automáticos. Si tu red tiene problemas persistentes
# para llegar a Open-Meteo (firewall, antivirus, ISP), esto no lo arregla —
# solo evita esperar de más: falla rápido y el modelo sigue sin el clima.
_session = requests.Session()
_session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=1, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504]
)))


def get_game_weather(lat: float, lon: float, game_datetime_iso: str) -> dict:
    """
    Devuelve temperatura (°F), velocidad de viento (mph) y dirección de
    viento (grados) para la hora más cercana al horario del juego.

    game_datetime_iso: string ISO 8601, ej. '2026-07-02T23:05:00Z'
    (el campo 'game_time' que ya devuelve data/mlb_api.py get_schedule).
    """
    result = {"temp_f": None, "wind_mph": None, "wind_direction_deg": None}

    if lat is None or lon is None or not game_datetime_iso:
        return result

    try:
        game_dt = datetime.fromisoformat(game_datetime_iso.replace("Z", "+00:00"))
    except ValueError:
        return result

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m",
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
        wind_dirs = data["hourly"]["wind_direction_10m"]

        # Encuentra la hora del pronóstico más cercana a la hora del juego
        target_hour = game_dt.strftime("%Y-%m-%dT%H:00")
        if target_hour in times:
            idx = times.index(target_hour)
        else:
            # Sin match exacto: la hora disponible más cercana en el tiempo,
            # no la primera del día (que podía estar a horas de distancia).
            game_dt_naive = game_dt.replace(tzinfo=None)
            hour_dts = [datetime.fromisoformat(t) for t in times]
            idx = min(range(len(hour_dts)), key=lambda i: abs((hour_dts[i] - game_dt_naive).total_seconds()))

        result["temp_f"] = temps[idx]
        result["wind_mph"] = winds[idx]
        result["wind_direction_deg"] = wind_dirs[idx]

    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.warning(f"No se pudo obtener clima ({lat},{lon}): {e}")

    return result


def preload_weather(games: list[dict], park_lookup) -> dict[int, dict]:
    """
    Trae el clima de todos los estadios de la jornada EN PARALELO, una sola
    vez por equipo local (varios juegos comparten fecha/ciudad, no hace
    falta pedirlo dos veces). Esto reemplaza N llamadas secuenciales (donde
    un timeout de 15s en una bloqueaba a las siguientes) por llamadas
    concurrentes con reintento automático.

    park_lookup: función tipo data.park_factors.get_park_info

    Devuelve: {home_team_id: {"temp_f":..., "wind_mph":..., "wind_direction_deg":...}}
    """
    unique_by_team: dict[int, tuple] = {}
    for g in games:
        team_id = g["home_team_id"]
        if team_id not in unique_by_team:
            park = park_lookup(team_id)
            unique_by_team[team_id] = (park["lat"], park["lon"], g["game_time"])

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
            except Exception as e:
                logger.warning(f"Clima falló para team_id={team_id}: {e}")
                results[team_id] = {"temp_f": None, "wind_mph": None, "wind_direction_deg": None}

    return results
