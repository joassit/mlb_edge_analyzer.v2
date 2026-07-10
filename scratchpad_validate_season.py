import json
import sys

sys.path.insert(0, ".")

SEASON = int(sys.argv[1]) if len(sys.argv) > 1 else 2022

from historical_engine.db import SessionLocal, HistoricalRun, HistoricalGame, HistoricalAnalysis, HistoricalPrediction
from historical_engine.validation import validate_all_sources

out = {}
session = SessionLocal()
try:
    runs = session.query(HistoricalRun).filter(HistoricalRun.season_year == SEASON).all()
    out["runs"] = [
        {
            "id": r.id, "run_type": r.run_type, "status": r.status,
            "n_games_processed": r.n_games_processed, "n_games_skipped": r.n_games_skipped,
            "n_errors": r.n_errors, "started_at": str(r.started_at), "completed_at": str(r.completed_at),
        }
        for r in runs
    ]
    if not runs:
        out["error"] = f"No hay HistoricalRun con season_year={SEASON}"
    else:
        run_id = runs[-1].id
        out["run_id_used"] = run_id
        out["n_historical_game"] = session.query(HistoricalGame).filter(HistoricalGame.run_id == run_id).count()
        out["n_historical_analysis"] = session.query(HistoricalAnalysis).filter(HistoricalAnalysis.run_id == run_id).count()
        out["n_historical_prediction"] = session.query(HistoricalPrediction).filter(HistoricalPrediction.run_id == run_id).count()
        status_rows = session.query(HistoricalGame.status).filter(HistoricalGame.run_id == run_id).all()
        counts = {}
        for (status,) in status_rows:
            counts[status] = counts.get(status, 0) + 1
        out["status_counts"] = counts
        try:
            out["validate_all_sources"] = validate_all_sources(SEASON, run_id)
        except Exception as e:
            out["validate_all_sources_error"] = str(e)
finally:
    session.close()

print("===JSON_VALIDATION_START===")
print(json.dumps(out, indent=2, default=str))
print("===JSON_VALIDATION_END===")
