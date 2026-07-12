import logging
import requests
from datetime import date
from config import MLB_API_BASE
from data.contracts import require, SchemaError
from data.http import session

logger = logging.getLogger("mlb_edge_analyzer")

def get_schedule(target_date: date = None) -> list[dict]:
    """Lista de juegos de la fecha dada. Devuelve [] si la API falla (red,
    timeout, esquema) -- nunca propaga la excepción hacia el pipeline."""
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
        logger.warning(f"No se pudo obtener el schedule de {target_date}: {e}")
        return []

    games = []
    for date_block in payload.get("dates", []):
        for g in date_block.get("games", []):
            parsed = _parse_game(g)
            if isinstance(parsed, SchemaError):
                # Un solo juego con esquema roto no debe tumbar el resto del
                # día — se salta ese juego y se sigue con los demás.
                logger.error(f"Juego omitido por cambio de esquema: {parsed}")
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

    detailed_state = require(g, ["status", "detailedState"], "schedule.game")
    if isinstance(detailed_state, SchemaError):
        return detailed_state
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
        "away_pitcher_name": away_pitcher["fullName"] if away_pitcher else None,
        "home_pitcher_id": home_pitcher["id"] if home_pitcher else None,
        "home_pitcher_name": home_pitcher["fullName"] if home_pitcher else None,
        "game_time": g.get("gameDate"),
        "game_date_official": g.get("officialDate"),
        "status": detailed_state,
        "abstract_state": abstract_state,
        # gameNumber/doubleHeader identifican juego 1 vs. juego 2 de una
        # doble cartelera -- cada uno trae su propio game_pk DISTINTO en
        # MLB Stats API, así que no hay forma de detectarlos por game_pk;
        # se usan opcionalmente (.get, no require()) para no volver esto un
        # campo obligatorio -- solo enriquecen el mensaje de descarte en
        # main.py::analyze_today() cuando aplica.
        "game_number": g.get("gameNumber"),
        "double_header": g.get("doubleHeader"),
    }

def get_game_result(game_pk: int) -> dict | None:
    """Resultado final del juego, o None si todavía no termina, se pospuso,
    o la API falla (red, timeout, esquema) -- las tres situaciones son
    indistinguibles para el caller, que de cualquier forma reintenta al
    día siguiente vía get_predictions_without_result()."""
    params = {"sportId": 1, "gamePk": game_pk, "hydrate": "linescore"}
    try:
        resp = session.get(f"{MLB_API_BASE}/schedule", params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"No se pudo obtener el resultado de game_pk={game_pk}: {e}")
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
        # abstractGameState="Final" con linescore vacío -- visto en la práctica en
        # juegos pospuestos que la API deja de considerar "en curso" (a veces bajo
        # una fecha distinta a la original, ver detailedState) sin nunca completar
        # un marcador bajo este game_pk. Se sigue devolviendo None (el caller
        # reintenta) pero se deja constancia en el log -- sin esto, estas filas
        # parecen "todavía no jugadas" indefinidamente en vez de "pospuestas sin
        # marcador reconciliable", que es la causa real.
        logger.warning(
            f"game_pk={game_pk}: abstractGameState=Final pero sin linescore "
            f"(detailedState={game['status'].get('detailedState')!r}) -- probable "
            f"juego pospuesto que no se completó bajo este game_pk, no un juego "
            f"todavía pendiente."
        )
        return None

    return {
        "home_score": home_score,
        "away_score": away_score,
        "winner": "home" if home_score > away_score else "away",
        "total_runs": home_score + away_score,
    }

if __name__ == "__main__":
    for g in get_schedule():
        print(f"{g['away_team']} @ {g['home_team']} — "
              f"{g['away_pitcher_name'] or 'TBD'} vs {g['home_pitcher_name'] or 'TBD'} "
              f"[{g['status']}]")