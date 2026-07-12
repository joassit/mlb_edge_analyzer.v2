import json

from data.http import session

resp = session.get(
    "https://statsapi.mlb.com/api/v1/teams/110/stats",
    params={"stats": "gameLog", "group": "hitting", "season": 2025},
    timeout=15,
)
splits = resp.json()["stats"][0]["splits"]

by_date = {}
for s in splits:
    by_date.setdefault(s.get("date"), []).append(s)

dupes = {d: rows for d, rows in by_date.items() if len(rows) > 1}
print("total_splits:", len(splits))
print("duplicate_dates:", list(dupes.keys())[:5])

if dupes:
    d = next(iter(dupes))
    print("===JSON_DIAG_START===")
    print(json.dumps(dupes[d], indent=2))
    print("===JSON_DIAG_END===")
else:
    print("===JSON_DIAG_START===")
    print(json.dumps(splits[0], indent=2))
    print("===JSON_DIAG_END===")
