"""
Clima del estadio vía Open-Meteo (https://open-meteo.com) — gratis, sin API key.

El clima solo tiene sentido para juegos de HOY o los próximos días (Open-Meteo
da pronóstico, no clima de un juego pasado). Para juegos ya jugados, esto
no aplica.
"""

from datetime import datetime
import logging
import requests
from functools import lru_cache

logger = logging.getLogger("mlb_edge_analyzer")

OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"


@lru_cache(maxsize=32)
def get_game_weather(lat: float, lon: float, game_datetime_iso: str) -> dict:
    """
    Devuelve temperatura (°F), velocidad de viento (mph) y dirección de
    viento (grados) para la hora más cercana al horario del juego.

    game_datetime_iso: string ISO 8601, ej. '2026-07-02T23:05:00Z'
    (el campo 'game_time' que ya devuelve data/mlb_api.py get_schedule).
    """
    # Si la API falla, devolvemos 72°F (clima de domo) en vez de None 
    # para que las fórmulas matemáticas del modelo no exploten más adelante.
    result = {"temp_f": 72.0, "wind_mph": 0.0, "wind_direction_deg": 0.0}

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
        # AQUI ESTÁ LA MAGIA: timeout de 3 segundos en lugar de 15
        resp = requests.get(OPEN_METEO_BASE, params=params, timeout=3)
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
            idx = 0  # fallback: primera hora del día si no hay match exacto

        # Actualizamos con los datos reales si todo salió bien
        if temps[idx] is not None:
            result["temp_f"] = temps[idx]
            result["wind_mph"] = winds[idx]
            result["wind_direction_deg"] = wind_dirs[idx]

    except requests.exceptions.Timeout:
        # Cambiamos warning por debug para no ensuciar la consola
        logger.debug(f"Timeout del clima en ({lat},{lon}). Usando datos por defecto (72°F).")
        
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.debug(f"No se pudo obtener clima ({lat},{lon}): {e}")

    return result
