import json
import sqlite3

from historical_engine.training import propose_starter_weight_recalibration

conn = sqlite3.connect("historical_backtest.db")
cur = conn.cursor()
season_to_run = dict(cur.execute(
    "SELECT season_year, MAX(id) FROM historical_run WHERE run_type='season' GROUP BY season_year"
).fetchall())
conn.close()

print("season_to_run:", season_to_run)

CANDIDATES = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.65, 0.7, 0.8, 0.9, 1.0]

results = {}
for season in sorted(season_to_run.keys()):
    run_id = season_to_run[season]
    results[season] = propose_starter_weight_recalibration(
        season_year=season, run_id=run_id, candidate_values=CANDIDATES,
    )

print("===JSON_STARTER_WEIGHT_SWEEP_START===")
print(json.dumps(results, indent=2, default=str))
print("===JSON_STARTER_WEIGHT_SWEEP_END===")
