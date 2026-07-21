"""Backfill real (nunca sintetico) de `historical_closer_leverage`: para
cada juego ya ingerido, re-deriva `closer_pitcher_id` (mismo criterio que
`point_in_time_provider.py::bullpen_era_as_of()` -- el relevista con mas
saves point-in-time del roster) y su IP en los `days` dias previos al
juego (`pitcher_recent_ip_as_of()`, ya real y probado, reusado tal cual
desde `historical/injuries.py`).

Costo real de red, explicito: `closer_pitcher_id` NO se persistio durante
la ingesta original (`GameSnapshot` solo guarda el booleano derivado
`closer_available`) -- este backfill tiene que re-pedir el roster +
stats de cada pitcher de bullpen por equipo por juego (mismo costo que
`bullpen_era_as_of()` durante la ingesta original), mas una llamada
adicional por cerrador identificado. Para 13,101 juegos x 2 equipos esto
es un volumen de llamadas comparable a una fraccion real de la ingesta
historica completa -- nunca se dispara sin confirmacion explicita, y se
recomienda una temporada de prueba antes de las 5 completas."""

from __future__ import annotations

import logging

from jsa.historical import db as historical_db
from jsa.historical.point_in_time_provider import HistoricalStatsProvider

logger = logging.getLogger("jsa.historical")

DEFAULT_LOOKBACK_DAYS = 2


def fetch_team_closer_leverage(provider: HistoricalStatsProvider, team_id: int, as_of_date: str, season: int, *, days: int = DEFAULT_LOOKBACK_DAYS) -> dict:
    """`closer_pitcher_id=None` si el roster no tiene ningun relevista con
    saves point-in-time (equipo sin cerrador definido todavia esa fecha,
    ej. principios de temporada) -- `closer_recent_ip` queda `None` en ese
    caso, nunca se aproxima a 0.0 (0.0 real y `None` "no aplica" son cosas
    distintas)."""
    bullpen = provider.bullpen_era_as_of(team_id, as_of_date, season) or {}
    closer_pitcher_id = bullpen.get("closer_pitcher_id")
    if closer_pitcher_id is None:
        return {"closer_pitcher_id": None, "closer_recent_ip": None}
    closer_recent_ip = provider.pitcher_recent_ip_as_of(closer_pitcher_id, as_of_date, days=days)
    return {"closer_pitcher_id": closer_pitcher_id, "closer_recent_ip": closer_recent_ip}


def backfill_season(engine, provider: HistoricalStatsProvider, season: int, *, days: int = DEFAULT_LOOKBACK_DAYS) -> dict:
    """Idempotente (`upsert_closer_leverage` -- ON CONFLICT DO NOTHING por
    `(game_pk, team_id)`): re-correr esta funcion nunca duplica trabajo ya
    hecho ni vuelve a gastar llamadas de red para juegos ya backfilleados."""
    historical_db.init_historical_storage(engine)
    games = historical_db.games_for_season(engine, season)
    already_done = {(row["game_pk"], row["team_id"]) for row in historical_db.closer_leverage_for_season(engine, season)}

    n_processed, n_skipped = 0, 0
    for game in games:
        game_date = game["game_date"].isoformat() if hasattr(game["game_date"], "isoformat") else str(game["game_date"])
        for side, team_id in (("home", game["home_team_id"]), ("away", game["away_team_id"])):
            if (game["game_pk"], team_id) in already_done:
                n_skipped += 1
                continue
            signal = fetch_team_closer_leverage(provider, team_id, game_date, season, days=days)
            historical_db.upsert_closer_leverage(
                engine, season=season, game_pk=game["game_pk"], team_id=team_id, side=side,
                closer_pitcher_id=signal["closer_pitcher_id"], closer_recent_ip=signal["closer_recent_ip"],
                lookback_days=days,
            )
            n_processed += 1

    logger.info("closer-leverage-backfill(%s) completo -- n_processed=%s n_skipped=%s", season, n_processed, n_skipped)
    return {"season": season, "n_games": len(games), "n_processed": n_processed, "n_skipped": n_skipped, "lookback_days": days}
