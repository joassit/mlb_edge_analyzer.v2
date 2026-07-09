"""
Prueba de humo de historical_engine/reports.py -- genera el HTML sin
crashear y confirma que el archivo queda escrito, con datos sembrados a mano.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import historical_engine.db as historical_db
from historical_engine.reports import generate_historical_report


def test_generate_historical_report_writes_html_file(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/reports_test.db")
    historical_db.HistoricalBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    for i, (source, home_prob, predicted, actual) in enumerate([
        ("skellam", 0.70, "home", "home"), ("skellam", 0.60, "home", "away"),
        ("negbin", 0.65, "home", "home"), ("heuristic", 0.55, "home", "away"),
    ]):
        session.add(historical_db.HistoricalPrediction(
            run_id=1, game_pk=i, game_date="2024-05-01", season_year=2024, source=source,
            away_prob=1 - home_prob, home_prob=home_prob,
            predicted_winner=predicted, actual_winner=actual, correct=predicted == actual,
        ))
    session.add(historical_db.HistoricalGame(
        run_id=1, game_pk=0, game_date="2024-05-01", season_year=2024,
        away_team="A", home_team="B", home_score=5, away_score=3, winner="home", total_runs=8,
    ))
    session.add(historical_db.HistoricalAnalysis(
        run_id=1, game_pk=0, game_date="2024-05-01", season_year=2024, as_of_date="2024-04-30",
        home_proj_runs=4.8, away_proj_runs=3.2,
    ))
    session.commit()
    session.close()

    output_dir = str(tmp_path / "report_out")
    path = generate_historical_report(season_year=2024, run_id=1, output_dir=output_dir, session_factory=Session)

    assert os.path.isfile(path)
    with open(path) as f:
        content = f.read()
    assert "Historical Backtesting Report" in content
    assert "temporada 2024" in content
