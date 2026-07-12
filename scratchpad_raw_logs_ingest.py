import json
import sqlite3
import sys

from historical_engine.db import init_historical_db
from historical_engine.raw_ingestion import ingest_raw_logs_for_season

# El artifact consolidado se generó ANTES de que esta sesión agregara
# HistoricalRawBattingLog/PitchingLog/RosterSnapshot/FetchLedger al esquema
# -- create_all() solo crea las tablas que faltan, nunca toca las que ya
# existen con datos.
init_historical_db()

season = int(sys.argv[1])

conn = sqlite3.connect("historical_backtest.db")
cur = conn.cursor()
season_to_run = dict(cur.execute(
    "SELECT season_year, MAX(id) FROM historical_run WHERE run_type='season' GROUP BY season_year"
).fetchall())
conn.close()

run_id = season_to_run[season]
print(f"season={season} run_id={run_id}")

result = ingest_raw_logs_for_season(season, run_id)

print("===JSON_RAW_LOGS_START===")
print(json.dumps(result, indent=2, default=str))
print("===JSON_RAW_LOGS_END===")
