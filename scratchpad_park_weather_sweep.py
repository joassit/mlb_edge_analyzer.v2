import json
import sqlite3

from historical_engine.training import propose_runs_projection_recalibration

conn = sqlite3.connect("historical_backtest.db")
cur = conn.cursor()
season_to_run = dict(cur.execute(
    "SELECT season_year, MAX(id) FROM historical_run WHERE run_type='season' GROUP BY season_year"
).fetchall())
conn.close()

print("season_to_run:", season_to_run)

PARK_CANDIDATES = [0.0, 0.5, 0.8, 1.0, 1.15, 1.3, 1.5, 2.0]
WEATHER_CANDIDATES = [-0.08, -0.05, -0.02, 0.0, 0.02, 0.05, 0.08]

results = {}
for season in sorted(season_to_run.keys()):
    run_id = season_to_run[season]
    results[season] = {
        "park_factor_weight": propose_runs_projection_recalibration(
            season_year=season, run_id=run_id, param_name="PARK_FACTOR_WEIGHT",
            candidate_values=PARK_CANDIDATES,
        ),
        "weather_correction": propose_runs_projection_recalibration(
            season_year=season, run_id=run_id, param_name="WEATHER_CORRECTION",
            candidate_values=WEATHER_CANDIDATES,
        ),
    }

print("===JSON_PARK_WEATHER_SWEEP_START===")
print(json.dumps(results, indent=2, default=str))
print("===JSON_PARK_WEATHER_SWEEP_END===")
