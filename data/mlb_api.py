"""
Cliente para la MLB Stats API (oficial, gratuita, sin API key).
Documentación no oficial de referencia: https://github.com/toddrob99/MLB-StatsAPI

Aquí obtenemos:
- Calendario del día (juegos programados)
- Pitchers probables (cuando ya están confirmados por los equipos)
"""

from datetime import date
import requests

from config import MLB_API_BASE


def get_schedule(target_date: date = None) -> list[dict]:
    """
    Devuelve la lista de juegos de un día, con pitchers probables cuando
    ya están confirmados. Si target_date es None, usa hoy.

    Cada elemento del resultado tiene:
    {
        "game_pk": int,
        "away_team": str,
        "home_team": str,
        "away_team_id": int,
        "home_team_id": int,
        "away_pitcher_id": int | None,
        "away_pitcher_name": str | None,
        "home_pitcher_id": int | None,
        "home_pitcher_name": str | None,
        "game_time": str,
        "status": str,
    }
    """
    if target_date is None:
        target_date = date.today()

    params = {
        "sportId": 1,  # MLB
        "date": target_date.strftime("%Y-%m-%d"),
        "hydrate": "probablePitcher,team,linescore",
    }

    resp = requests.get(f"{MLB_API_BASE}/schedule", params=params, timeout=15)
    resp.raise_for_status()
    payload = resp.json()

    games = []
    seen_pks = set()
    for date_block in payload.get("dates", []):
        for g in date_block.get("games", []):
            if g["gamePk"] in seen_pks:
                continue
            seen_pks.add(g["gamePk"])

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
                "abstract_state": g["status"]["abstractGameState"],  # "Preview" | "Live" | "Final"
            })

    return games


def get_game_result(game_pk: int) -> dict | None:
    """
    Resultado final de un juego ya jugado. Devuelve None si el juego
    todavía no termina (o no existe).

    {"home_score": int, "away_score": int, "winner": "home"|"away", "total_runs": int}
    """
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


def get_actual_starters(game_pk: int) -> dict | None:
    """
    Los pitchers que REALMENTE abrieron un juego ya jugado (del boxscore),
    para detectar 'pitcher scratch': cuando el abridor probable de la mañana
    fue reemplazado antes del juego, la predicción quedó basada en un pitcher
    que no lanzó y no debe calificarse como acierto/fallo del modelo.

    Devuelve {"away_starter_id": int|None, "home_starter_id": int|None}
    o None si el boxscore no está disponible.
    """
    try:
        resp = requests.get(
            f"{MLB_API_BASE.replace('/v1', '/v1.1')}/game/{game_pk}/feed/live",
            timeout=15,
        )
        resp.raise_for_status()
        boxscore = resp.json()["liveData"]["boxscore"]["teams"]

        def first_pitcher(side: str) -> int | None:
            pitcher_ids = boxscore[side].get("pitchers", [])
            return pitcher_ids[0] if pitcher_ids else None

        return {
            "away_starter_id": first_pitcher("away"),
            "home_starter_id": first_pitcher("home"),
        }
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None


def get_actual_starters(game_pk: int) -> dict | None:
    """
    IDs de los pitchers que REALMENTE abrieron un juego ya jugado
    (del boxscore). Sirve para detectar 'pitcher scratch': cuando el
    abridor probable de la mañana fue reemplazado antes del juego.

    Devuelve: {"away_starter_id": int|None, "home_starter_id": int|None}
    o None si el boxscore no está disponible.
    """
    try:
        resp = requests.get(f"{MLB_API_BASE}/game/{game_pk}/boxscore", timeout=15)
        resp.raise_for_status()
        box = resp.json()
    except requests.RequestException:
        return None

    result = {"away_starter_id": None, "home_starter_id": None}
    for side in ("away", "home"):
        try:
            pitcher_ids = box["teams"][side].get("pitchers", [])
            # El primer pitcher de la lista del boxscore es el abridor
            if pitcher_ids:
                result[f"{side}_starter_id"] = pitcher_ids[0]
        except (KeyError, TypeError):
            continue

    return result


if __name__ == "__main__":
    for g in get_schedule():
        print(f"{g['away_team']} @ {g['home_team']} — "
              f"{g['away_pitcher_name'] or 'TBD'} vs {g['home_pitcher_name'] or 'TBD'} "
              f"[{g['status']}]")
