"""Trae el schedule + resultados finales de una temporada completa, en un
rango de fechas (no dia a dia -- una sola llamada a la MLB Stats API con
`startDate`/`endDate` trae todos los juegos del rango con su resultado ya
incluido via `hydrate=linescore`, mucho mas eficiente para ingesta masiva
que golpear el endpoint juego por juego).

Deliberadamente NO reusa `jsa/data_sources/mlb_api.py::get_schedule()`:
esa funcion excluye todo lo que no este en `Preview` (correcto para
produccion en vivo, que nunca debe evaluar un juego ya jugado) -- aqui es
exactamente al reves, solo interesan los juegos ya `Final`."""

from __future__ import annotations

import logging
from datetime import date

import requests

from jsa.data_sources.contracts import SchemaError, require
from jsa.data_sources.http import session
from jsa.historical.config import CURRENT_SEASON, INGESTION_REQUEST_TIMEOUT, MLB_API_BASE

logger = logging.getLogger("jsa.historical")


def season_date_range(season: int) -> tuple[str, str]:
    """Rango conservador de temporada regular + postemporada -- pedir de
    mas (spring training de marzo, offseason de diciembre) no genera
    fuga, solo trae dias sin juegos que la API responde vacios. Para la
    temporada EN CURSO, `end_date` se acota a hoy (nunca se pide el
    calendario completo de un año que todavia no termino)."""
    start = f"{season}-03-01"
    if season >= CURRENT_SEASON:
        end = date.today().isoformat()
    else:
        end = f"{season}-12-01"
    return start, end


def fetch_season_games(season: int) -> list[dict]:
    """Todos los juegos `Final` de la temporada, con resultado incluido.
    Devuelve [] si la API falla -- nunca propaga la excepcion (una
    temporada de ingesta no debe morir por un fallo transitorio de red a
    mitad de camino; el caller decide si reintentar)."""
    start_date, end_date = season_date_range(season)
    params = {
        "sportId": 1, "startDate": start_date, "endDate": end_date,
        "hydrate": "probablePitcher,team,linescore",
    }
    try:
        resp = session.get(f"{MLB_API_BASE}/schedule", params=params, timeout=INGESTION_REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("No se pudo obtener el schedule de la temporada %s (%s a %s): %s", season, start_date, end_date, e)
        return []

    games: list[dict] = []
    for date_block in payload.get("dates", []):
        for g in date_block.get("games", []):
            parsed = _parse_finished_game(g, season)
            if parsed is None:
                continue
            games.append(parsed)
    logger.info("Temporada %s: %d juegos Final encontrados (%s a %s).", season, len(games), start_date, end_date)
    return games


def build_previous_park_index(games: list[dict]) -> dict[tuple[int, int], int]:
    """Para cada (team_id, game_pk), el team_id del estadio donde ese
    equipo jugo su partido INMEDIATO anterior en la temporada -- alimenta
    `travel_distance` (Seccion 5: "el visitante es quien viaja") sin pedir
    ninguna llamada de red adicional: `games` ya viene de `fetch_season_games()`,
    una sola vez por temporada, y este indice se calcula enteramente en
    memoria a partir de ahi.

    Ausente del dict (no `None` explicito) para el primer juego de la
    temporada de un equipo -- no hay partido anterior que consultar.

    Defensivo ante entradas malformadas (campos faltantes) -- se saltan en
    vez de propagar, mismo criterio que el resto de la ingesta: un juego
    con datos incompletos nunca debe tumbar el calculo de toda la
    temporada (`pipeline.py` ya aisla fallas por juego DENTRO del loop
    principal; este indice se construye ANTES de ese loop, asi que
    necesita su propio aislamiento)."""
    by_team: dict[int, list[tuple[str, int, int]]] = {}
    for g in games:
        game_date, game_pk = g.get("game_date"), g.get("game_pk")
        home_team_id, away_team_id = g.get("home_team_id"), g.get("away_team_id")
        if game_date is None or game_pk is None or home_team_id is None or away_team_id is None:
            continue
        for team_id in (home_team_id, away_team_id):
            by_team.setdefault(team_id, []).append((game_date, game_pk, home_team_id))

    index: dict[tuple[int, int], int] = {}
    for team_id, entries in by_team.items():
        entries.sort(key=lambda e: (e[0], e[1]))  # fecha, luego game_pk (orden estable en doble cartelera)
        previous_location: int | None = None
        for _game_date, game_pk, location_team_id in entries:
            if previous_location is not None:
                index[(team_id, game_pk)] = previous_location
            previous_location = location_team_id
    return index


def _parse_finished_game(g: dict, season: int) -> dict | None:
    abstract_state = g.get("status", {}).get("abstractGameState")
    if abstract_state != "Final":
        return None

    game_pk = require(g, ["gamePk"], "historical.schedule.game")
    away = require(g, ["teams", "away"], "historical.schedule.game")
    home = require(g, ["teams", "home"], "historical.schedule.game")
    if isinstance(game_pk, SchemaError) or isinstance(away, SchemaError) or isinstance(home, SchemaError):
        logger.error("Juego historico omitido por cambio de esquema: %s", g.get("gamePk"))
        return None

    away_team_id = away.get("team", {}).get("id")
    home_team_id = home.get("team", {}).get("id")
    if away_team_id is None or home_team_id is None:
        return None

    linescore = g.get("linescore", {}).get("teams", {})
    home_score = linescore.get("home", {}).get("runs")
    away_score = linescore.get("away", {}).get("runs")
    if home_score is None or away_score is None:
        # Final sin linescore reconciliable -- mismo caso documentado en
        # jsa/data_sources/mlb_api.py::get_game_result (juego pospuesto
        # que no se completo bajo este game_pk). Se omite: no hay
        # resultado real que backtest pueda usar.
        return None

    away_pitcher = away.get("probablePitcher")
    home_pitcher = home.get("probablePitcher")
    official_date = g.get("officialDate")

    return {
        "game_pk": game_pk,
        "season": season,
        "game_date": official_date or date.today().isoformat(),
        "home_team": home.get("team", {}).get("name"),
        "away_team": away.get("team", {}).get("name"),
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "home_pitcher_id": home_pitcher["id"] if home_pitcher else None,
        "away_pitcher_id": away_pitcher["id"] if away_pitcher else None,
        "is_double_header": g.get("gameNumber", 1) > 1 or g.get("doubleHeader") == "Y",
        "home_score": home_score,
        "away_score": away_score,
    }
