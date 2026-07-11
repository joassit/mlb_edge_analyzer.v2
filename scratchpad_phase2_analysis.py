"""
Script temporal -- corre DENTRO de un workflow de GitHub Actions contra el
historical_backtest.db consolidado (2022-2025, ver scratchpad_merge_historical_dbs.py)
para completar la fase 2: validate + compare por temporada, drift por
motor a través de las 4 temporadas, propuesta de recalibración (train) y
el reporte histórico final con comparación entre temporadas.
"""
import json
import sqlite3

from historical_engine.validation import validate_all_sources, compare_seasons_drift
from historical_engine.model_comparison import compare_models
from historical_engine.training import propose_dispersion_recalibration
from historical_engine.reports import generate_historical_report

conn = sqlite3.connect("historical_backtest.db")
cur = conn.cursor()
season_to_run = dict(cur.execute(
    "SELECT season_year, MAX(id) FROM historical_run WHERE run_type='season' GROUP BY season_year"
).fetchall())
conn.close()

print("season_to_run:", season_to_run)
seasons = sorted(season_to_run.keys())

results = {"season_to_run": season_to_run, "validate": {}, "compare": {}, "drift": {}}

for season in seasons:
    run_id = season_to_run[season]
    results["validate"][season] = validate_all_sources(season, run_id)

for season in seasons:
    run_id = season_to_run[season]
    results["compare"][season] = compare_models(season, run_id)

for source in ("heuristic", "skellam", "negbin"):
    results["drift"][source] = compare_seasons_drift(source, seasons)

last_season = max(seasons)
last_run_id = season_to_run[last_season]
results["train"] = propose_dispersion_recalibration(last_season, last_run_id)

print("===JSON_PHASE2_START===")
print(json.dumps(results, indent=2, default=str))
print("===JSON_PHASE2_END===")

other_seasons = [s for s in seasons if s != last_season]
report_path = generate_historical_report(
    last_season, last_run_id, "historical_reports",
    other_seasons_for_drift=other_seasons,
)
print("report_path:", report_path)
