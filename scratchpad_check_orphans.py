import json
import sqlite3
import urllib.request

conn = sqlite3.connect("mlb_edge.db")
cur = conn.cursor()

cur.execute("SELECT DISTINCT game_pk, away_team, home_team FROM game_analysis WHERE game_date='2026-07-07'")
rows_0707 = cur.fetchall()
cur.execute("SELECT game_pk FROM actual_results")
result_pks = {r[0] for r in cur.fetchall()}
orphans_0707 = [r for r in rows_0707 if r[0] not in result_pks]

out = {"orphans_0707": orphans_0707, "known_orphan_0710": 823357}

game_pks_to_check = [o[0] for o in orphans_0707] + [823357]

checked = []
for pk in game_pks_to_check:
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&gamePk={pk}&hydrate=linescore"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            payload = json.loads(resp.read())
        dates = payload.get("dates", [])
        if not dates or not dates[0].get("games"):
            checked.append({"game_pk": pk, "status": "NO_DATA"})
            continue
        g = dates[0]["games"][0]
        checked.append({
            "game_pk": pk,
            "date": dates[0]["date"],
            "away": g["teams"]["away"]["team"]["name"],
            "home": g["teams"]["home"]["team"]["name"],
            "abstractGameState": g["status"]["abstractGameState"],
            "detailedState": g["status"].get("detailedState"),
            "linescore": g.get("linescore", {}).get("teams", {}),
        })
    except Exception as e:
        checked.append({"game_pk": pk, "error": str(e)})

out["checked"] = checked

print("===JSON_ORPHANS_START===")
print(json.dumps(out, indent=2, default=str))
print("===JSON_ORPHANS_END===")
