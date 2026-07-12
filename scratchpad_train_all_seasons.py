import json
import sqlite3

from historical_engine.training import propose_dispersion_recalibration

conn = sqlite3.connect("historical_backtest.db")
cur = conn.cursor()
season_to_run = dict(cur.execute(
    "SELECT season_year, MAX(id) FROM historical_run WHERE run_type='season' GROUP BY season_year"
).fetchall())
conn.close()

print("season_to_run:", season_to_run)

results = {}
for season in sorted(season_to_run.keys()):
    run_id = season_to_run[season]
    results[season] = propose_dispersion_recalibration(season, run_id)

print("===JSON_TRAIN_ALL_START===")
print(json.dumps(results, indent=2, default=str))
print("===JSON_TRAIN_ALL_END===")
