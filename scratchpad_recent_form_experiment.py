import json
import sqlite3
import sys

from historical_engine.recent_form import evaluate_recent_form

season = int(sys.argv[1])

conn = sqlite3.connect("historical_backtest.db")
cur = conn.cursor()
season_to_run = dict(cur.execute(
    "SELECT season_year, MAX(id) FROM historical_run WHERE run_type='season' GROUP BY season_year"
).fetchall())
n_raw = {
    t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    for t in ("historical_raw_batting_log", "historical_raw_pitching_log", "historical_raw_roster_snapshot")
}
conn.close()

run_id = season_to_run[season]
print(f"season={season} run_id={run_id} raw_counts={n_raw}")

WINDOWS = [15, 30]
WEIGHTS = [0.3, 0.5, 0.7, 1.0]

results = {}
for window_days in WINDOWS:
    results[f"window_{window_days}d"] = evaluate_recent_form(
        season_year=season, run_id=run_id, window_days=window_days, blend_weights=WEIGHTS,
    )

print("===JSON_RECENT_FORM_START===")
print(json.dumps(results, indent=2, default=str))
print("===JSON_RECENT_FORM_END===")
