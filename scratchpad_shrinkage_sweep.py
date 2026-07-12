import json
import sqlite3

from historical_engine.training import propose_probability_shrinkage

conn = sqlite3.connect("historical_backtest.db")
cur = conn.cursor()
season_to_run = dict(cur.execute(
    "SELECT season_year, MAX(id) FROM historical_run WHERE run_type='season' GROUP BY season_year"
).fetchall())
conn.close()

print("season_to_run:", season_to_run)

ALPHAS = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

results = {}
for season in sorted(season_to_run.keys()):
    run_id = season_to_run[season]
    results[season] = {
        source: propose_probability_shrinkage(
            source=source, season_year=season, run_id=run_id,
            candidate_values=ALPHAS,
        )
        for source in ("heuristic", "skellam", "negbin")
    }

print("===JSON_SHRINKAGE_START===")
print(json.dumps(results, indent=2, default=str))
print("===JSON_SHRINKAGE_END===")
