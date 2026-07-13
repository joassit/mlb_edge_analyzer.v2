"""Schedule, abridores probables y resultados finales via MLB Stats API
(oficial, gratuita, sin API key)."""

from __future__ import annotations

import logging
from datetime import date

import requests

from jsa.config import MLB_API_BASE
from jsa.data_sources.contracts import SchemaError, require
from jsa.data_sources.http import session

logger = logging.getLogger("jsa")


def get_schedule(target_date: date | None = None) -> list[dict]:
    """Lista de juegos de la fecha dada, EXCLUYENDO cualquier juego que no
    este en estado `Preview` (nunca `Final`/`In Progress`/etc.) -- la
    leccion mas cara de `mlb_edge_analyzer.v2/main.py`: analizar un juego ya
    decidido contamina la evaluacion con su propio resultado ya conocido.
    Devuelve [] si la API falla (red, timeout, esquema) -- nunca propaga."""
    if target_date is None:
        target_date = date.today()

    params = {
        "sportId": 1,
        "date": target_date.strftime("%Y-%m-%d"),
        "hydrate": "probablePitcher,team,linescore",
    }

    try:
        resp = session.get(f"{MLB_API_BASE}/schedule", params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("No se pudo obtener el schedule de %s: %s", target_date, e)
        return []

    games = []
    for date_block in payload.get("dates", []):
        for g in date_block.get("games", []):
            parsed = _parse_game(g)
            if isinstance(parsed, SchemaError):
                logger.error("Juego omitido por cambio de esquema: %s", parsed)
                continue
            if parsed["abstract_state"] != "Preview":
                logger.info(
                    "Juego %s excluido: abstract_state=%s (solo se analizan juegos en Preview, "
                    "nunca uno ya Final/en curso -- evita contaminar la evaluacion con su propio "
                    "resultado ya conocido).",
                    parsed["game_pk"],
                    parsed["abstract_state"],
                )
                continue
            games.append(parsed)
    return games


def _parse_game(g: dict) -> dict | SchemaError:
    game_pk = require(g, ["gamePk"], "schedule.game")
    if isinstance(game_pk, SchemaError):
        return game_pk

    away = require(g, ["teams", "away"], "schedule.game")
    if isinstance(away, SchemaError):
        return away
    home = require(g, ["teams", "home"], "schedule.game")
    if isinstance(home, SchemaError):
        return home

    away_team_id = require(away, ["team", "id"], "schedule.game.teams.away")
    if isinstance(away_team_id, SchemaError):
        return away_team_id
    home_team_id = require(home, ["team", "id"], "schedule.game.teams.home")
    if isinstance(home_team_id, SchemaError):
        return home_team_id

    abstract_state = require(g, ["status", "abstractGameState"], "schedule.game")
    if isinstance(abstract_state, SchemaError):
        return abstract_state

    away_pitcher = away.get("probablePitcher")
    home_pitcher = home.get("probablePitcher")

    return {
        "game_pk": game_pk,
        "away_team": away.get("team", {}).get("name"),
        "home_team": home.get("team", {}).get("name"),
        "away_team_id": away_team_id,
        "home_team_id": home_team_id,
        "away_pitcher_id": away_pitcher["id"] if away_pitcher else None,
        "home_pitcher_id": home_pitcher["id"] if home_pitcher else None,
        "game_time": g.get("gameDate"),
        "game_date_official": g.get("officialDate"),
        "abstract_state": abstract_state,
        "is_double_header": g.get("gameNumber", 1) > 1 or g.get("doubleHeader") == "Y",
    }


def get_game_result(game_pk: int) -> dict | None:
    """Resultado final del juego, o None si todavia no termina o la API
    falla -- ambas situaciones son indistinguibles para el caller."""
    params = {"sportId": 1, "gamePk": game_pk, "hydrate": "linescore"}
    try:
        resp = session.get(f"{MLB_API_BASE}/schedule", params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("No se pudo obtener el resultado de game_pk=%s: %s", game_pk, e)
        return None

    dates = payload.get("dates", [])
    if not dates or not dates[0].get("games"):
        return None

    game = dates[0]["games"][0]
    if game["status"]["abstractGameState"] != "Final":
        return None

    linescore = game.get("linescore", {}).get("teams", {})
    home_score = linescore.get("home", {}).get("runs")
    away_score = linescore.get("away", {}).get("runs")
    if home_score is None or away_score is None:
        logger.warning(
            "game_pk=%s: abstractGameState=Final pero sin linescore -- probable juego "
            "pospuesto que no se completo bajo este game_pk.",
            game_pk,
        )
        return None

    return {
        "home_score": home_score,
        "away_score": away_score,
        "winner": "home" if home_score > away_score else "away",
        "total_runs": home_score + away_score,
    }
