import logging
from datetime import date
from config import MLB_API_BASE
from data.contracts import require, SchemaError
from data.http import session

logger = logging.getLogger("mlb_edge_analyzer")

def get_schedule(target_date: date = None) -> list[dict]:
    if target_date is None:
        target_date = date.today()

    params = {
        "sportId": 1,
        "date": target_date.strftime("%Y-%m-%d"),
        "hydrate": "probablePitcher,team,linescore",
    }

    resp = session.get(f"{MLB_API_BASE}/schedule", params=params, timeout=15)
    resp.raise_for_status()
    payload = resp.json()

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
    }

def get_game_result(game_pk: int) -> dict | None:
    params = {"sportId": 1, "gamePk": game_pk, "hydrate": "linescore"}
    resp = session.get(f"{MLB_API_BASE}/schedule", params=params, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    
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