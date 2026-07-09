"""
Pruebas de historical_engine/runs_analysis.py -- datos sembrados a mano,
verifica métricas y que los 3 PNG se generen en disco.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import historical_engine.db as historical_db
from historical_engine.runs_analysis import analyze_runs_projection


def _seeded(tmp_path, name):
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db")
    historical_db.HistoricalBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    games = [
        (1, 5, 4, 90.0),  # proj total 9 real total 9 -> error 0
        (2, 4, 3, 65.0),  # proj total 7 real total 6 -> error +1
        (3, 6, 5, 55.0),  # proj total 11 real total 9 -> error +2
        (4, 3, 2, 85.0),
        (5, 5, 3, 40.0),
        (6, 4, 4, 92.0),
    ]
    for game_pk, home_proj, away_proj, temp in games:
        session.add(historical_db.HistoricalGame(
            run_id=1, game_pk=game_pk, game_date="2024-05-01", season_year=2024,
            away_team="A", home_team="B", home_score=home_proj, away_score=away_proj,
            winner="home" if home_proj > away_proj else "away", total_runs=home_proj + away_proj,
        ))
        session.add(historical_db.HistoricalAnalysis(
            run_id=1, game_pk=game_pk, game_date="2024-05-01", season_year=2024, as_of_date="2024-04-30",
            home_proj_runs=float(home_proj), away_proj_runs=float(away_proj),
            home_skellam_prob=0.6, temp_f=temp,
        ))
    session.commit()
    session.close()
    return Session


def test_analyze_runs_projection_computes_metrics_and_saves_plots(tmp_path):
    Session = _seeded(tmp_path, "runs_basic")
    output_dir = str(tmp_path / "plots")

    metrics = analyze_runs_projection(run_id=1, output_dir=output_dir, session_factory=Session)

    assert metrics["n"] == 6
    assert metrics["total_mae"] is not None
    assert metrics["total_rmse"] is not None
    assert metrics["total_r2"] is not None
    assert os.path.isfile(metrics["plots"]["histogram"])
    assert os.path.isfile(metrics["plots"]["qq_plot"])
    assert os.path.isfile(metrics["plots"]["scatter"])


def test_analyze_runs_projection_perfect_predictions_give_zero_error(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/runs_perfect.db")
    historical_db.HistoricalBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add(historical_db.HistoricalGame(
        run_id=1, game_pk=1, game_date="2024-05-01", season_year=2024,
        away_team="A", home_team="B", home_score=5, away_score=4, winner="home", total_runs=9,
    ))
    session.add(historical_db.HistoricalAnalysis(
        run_id=1, game_pk=1, game_date="2024-05-01", season_year=2024, as_of_date="2024-04-30",
        home_proj_runs=5.0, away_proj_runs=4.0,
    ))
    session.commit()
    session.close()

    metrics = analyze_runs_projection(run_id=1, output_dir=str(tmp_path / "plots2"), session_factory=Session)
    assert metrics["total_mae"] == 0.0
    assert metrics["total_bias"] == 0.0


def test_analyze_runs_projection_handles_empty_history_without_crashing(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/runs_empty.db")
    historical_db.HistoricalBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    metrics = analyze_runs_projection(run_id=999, output_dir=str(tmp_path / "plots3"), session_factory=Session)
    assert metrics["n"] == 0
    assert "warning" in metrics
