"""
Script temporal (corre DENTRO de un workflow de GitHub Actions, con acceso
de red real a statsapi.mlb.com que este sandbox no tiene) para investigar
la causa raiz de las filas huerfanas (game_analysis sin ActualResult)
documentadas en los ultimos informes: 2026-07-07, 07-10, 07-17, 07-18.

Metodologia:
1. Recalcula la lista de huerfanos contra la mlb_edge.db real del
   artifact de hoy (no confia en listas armadas a mano) usando el mismo
   criterio que tracking/results_tracker.get_predictions_without_result():
   game_analysis (deduplicado por game_pk+game_date, fila mas reciente)
   sin fila correspondiente en actual_results.
2. Para cada huerfano, llama exactamente el mismo endpoint que usa
   data.mlb_api.get_game_result() (schedule?gamePk=X&hydrate=linescore)
   para ver el estado real hoy.
3. Ademas busca, por equipos+rango de fechas cercano, si existe un juego
   de reposicion (makeup game) bajo un game_pk DISTINTO -- confirma o
   refuta la hipotesis de "pospuesto y reprogramado con otro game_pk"
   que ya esta documentada como comentario en get_game_result().
"""
import json
import sys

import requests

sys.path.insert(0, ".")

from config import MLB_API_BASE
from db.database import SessionLocal, GameAnalysis, ActualResult

session = SessionLocal()
try:
    all_analysis = session.query(GameAnalysis).order_by(GameAnalysis.game_date, GameAnalysis.id).all()
    by_key = {}
    for r in all_analysis:
        k = (r.game_pk, r.game_date)
        if k not in by_key or r.id > by_key[k].id:
            by_key[k] = r

    ar_keys = {(r.game_pk, r.game_date) for r in session.query(ActualResult).all()}
    orphans = [r for k, r in by_key.items() if k not in ar_keys]
finally:
    session.close()

out = {"orphans_found": len(orphans), "details": []}

for r in sorted(orphans, key=lambda r: r.game_date):
    entry = {
        "game_date": r.game_date, "game_pk": r.game_pk,
        "away_team": r.away_team, "home_team": r.home_team,
        "model_version": r.model_version, "created_at": str(r.created_at),
    }

    # 1. Mismo llamado exacto que get_game_result()
    try:
        resp = requests.get(
            f"{MLB_API_BASE}/schedule",
            params={"sportId": 1, "gamePk": r.game_pk, "hydrate": "linescore"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        dates = payload.get("dates", [])
        if dates and dates[0].get("games"):
            g = dates[0]["games"][0]
            entry["current_status"] = {
                "abstractGameState": g["status"].get("abstractGameState"),
                "detailedState": g["status"].get("detailedState"),
                "codedGameState": g["status"].get("codedGameState"),
                "reason": g["status"].get("reason"),
                "gameDate": g.get("gameDate"),
                "rescheduleDate": g.get("rescheduleDate"),
                "rescheduleGameDate": g.get("rescheduleGameDate"),
                "gameNumber": g.get("gameNumber"),
                "doubleHeader": g.get("doubleHeader"),
                "linescore_home_runs": g.get("linescore", {}).get("teams", {}).get("home", {}).get("runs"),
                "linescore_away_runs": g.get("linescore", {}).get("teams", {}).get("away", {}).get("runs"),
            }
        else:
            entry["current_status"] = {"error": "sin datos en /schedule para este gamePk"}
    except Exception as e:
        entry["current_status"] = {"error": str(e)}

    # 2. Buscar juego de reposicion bajo OTRO game_pk: mismo par de
    # equipos, ventana de +/- 30 dias alrededor de game_date original.
    try:
        away_team = r.away_team
        home_team = r.home_team
        from datetime import date as _date, timedelta as _td
        base = _date.fromisoformat(r.game_date)
        start = (base - _td(days=2)).isoformat()
        end = (base + _td(days=35)).isoformat()
        resp2 = requests.get(
            f"{MLB_API_BASE}/schedule",
            params={"sportId": 1, "startDate": start, "endDate": end, "hydrate": "team,linescore"},
            timeout=20,
        )
        resp2.raise_for_status()
        payload2 = resp2.json()
        makeups = []
        for d in payload2.get("dates", []):
            for g in d.get("games", []):
                gpk = g.get("gamePk")
                if gpk == r.game_pk:
                    continue
                a = g.get("teams", {}).get("away", {}).get("team", {}).get("name")
                h = g.get("teams", {}).get("home", {}).get("team", {}).get("name")
                if a == away_team and h == home_team:
                    makeups.append({
                        "game_pk": gpk, "officialDate": g.get("officialDate"),
                        "abstractGameState": g.get("status", {}).get("abstractGameState"),
                        "detailedState": g.get("status", {}).get("detailedState"),
                        "home_runs": g.get("linescore", {}).get("teams", {}).get("home", {}).get("runs"),
                        "away_runs": g.get("linescore", {}).get("teams", {}).get("away", {}).get("runs"),
                    })
        entry["possible_makeup_games_same_matchup"] = makeups
    except Exception as e:
        entry["possible_makeup_games_same_matchup_error"] = str(e)

    out["details"].append(entry)

print("===ORPHAN_INVESTIGATION_JSON_START===")
print(json.dumps(out, indent=1, default=str))
print("===ORPHAN_INVESTIGATION_JSON_END===")
