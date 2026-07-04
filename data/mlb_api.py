from datetime import date
import requests
from config import MLB_API_BASE

def get_schedule(target_date: date = None) -> list[dict]:
    if target_date is None:
        target_date = date.today()

    params = {
        "sportId": 1,
        "date": target_date.strftime("%Y-%m-%d"),
        "hydrate": "probablePitcher,team,linescore",
    }

    resp = requests.get(f"{MLB_API_BASE}/schedule", params=params, timeout=15)
    resp.raise_for_status()
    payload = resp.json()

    games = []
    for date_block in payload.get("dates", []):
        for g in date_block.get("games", []):
            away = g["teams"]["away"]
            home = g["teams"]["home"]
            away_pitcher = away.get("probablePitcher")
            home_pitcher = home.get("probablePitcher")

            games.append({
                "game_pk": g["gamePk"],
                "away_team": away["team"]["name"],
                "home_team": home["team"]["name"],
                "away_team_id": away["team"]["id"],
                "home_team_id": home["team"]["id"],
                "away_pitcher_id": away_pitcher["id"] if away_pitcher else None,
                "away_pitcher_name": away_pitcher["fullName"] if away_pitcher else None,
                "home_pitcher_id": home_pitcher["id"] if home_pitcher else None,
                "home_pitcher_name": home_pitcher["fullName"] if home_pitcher else None,
                "game_time": g.get("gameDate"),
                "status": g["status"]["detailedState"],
                "abstract_state": g["status"]["abstractGameState"],
            })
    return games

def get_game_result(game_pk: int) -> dict | None:
    params = {"sportId": 1, "gamePk": game_pk, "hydrate": "linescore"}
    resp = requests.get(f"{MLB_API_BASE}/schedule", params=params, timeout=15)
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