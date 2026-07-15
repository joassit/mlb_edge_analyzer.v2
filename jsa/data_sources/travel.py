"""Distancia de viaje del equipo visitante -- Seccion 5 del spec ("el
visitante es quien viaja"). Mismo patron de `weather.py::preload_weather`:
una consulta por equipo UNICO de la jornada (no por juego), en paralelo,
para no repetir la misma llamada si dos juegos del dia comparten visitante
(virtualmente imposible, pero mismo criterio defensivo)."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from jsa.data_sources import mlb_api, park_factors

logger = logging.getLogger("jsa")


def preload_travel_distances(games: list[dict], target_date: date) -> dict[int, float | None]:
    """Devuelve {away_team_id: millas viajadas desde su partido anterior
    hasta el estadio de HOY (el del equipo local de ese juego)}, para
    todos los equipos visitantes UNICOS de `games`. `None` si no hay
    partido anterior conocido en la ventana de busqueda (nunca se
    aproxima)."""
    # today_home_by_away: un visitante juega como maximo un partido por
    # dia, asi que su home_team_id de HOY es unico -- security defensiva
    # igual que weather.py, no una suposicion nueva.
    today_home_by_away = {
        g["away_team_id"]: g["home_team_id"]
        for g in games
        if g.get("away_team_id") is not None and g.get("home_team_id") is not None
    }

    results: dict[int, float | None] = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_team = {
            executor.submit(mlb_api.get_previous_game_location, away_team_id, target_date): away_team_id
            for away_team_id in today_home_by_away
        }
        for future in future_to_team:
            away_team_id = future_to_team[future]
            try:
                previous_park_team_id = future.result()
            except Exception as e:  # noqa: BLE001 -- aislar fallas por equipo, nunca tumbar el lote
                logger.warning("Distancia de viaje fallo para team_id=%s: %s", away_team_id, e)
                previous_park_team_id = None
            today_home_team_id = today_home_by_away[away_team_id]
            results[away_team_id] = (
                park_factors.distance_miles(previous_park_team_id, today_home_team_id)
                if previous_park_team_id is not None
                else None
            )

    return results
