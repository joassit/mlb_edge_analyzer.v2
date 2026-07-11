import json
import urllib.request

url = (
    "https://statsapi.mlb.com/api/v1/schedule"
    "?sportId=1&teamId=119&startDate=2026-07-08&endDate=2026-07-11&hydrate=probablePitcher"
)
with urllib.request.urlopen(url, timeout=30) as resp:
    data = json.loads(resp.read())

out = []
for d in data.get("dates", []):
    for g in d.get("games", []):
        away = g["teams"]["away"]["team"]["name"]
        home = g["teams"]["home"]["team"]["name"]
        out.append({
            "date": d["date"],
            "game_pk": g["gamePk"],
            "away": away,
            "home": home,
            "status": g["status"]["abstractGameState"],
            "detailedState": g["status"].get("detailedState"),
            "away_probable": g["teams"]["away"].get("probablePitcher", {}).get("fullName"),
            "home_probable": g["teams"]["home"].get("probablePitcher", {}).get("fullName"),
        })

print("===JSON_DODGERS_START===")
print(json.dumps(out, indent=2))
print("===JSON_DODGERS_END===")
