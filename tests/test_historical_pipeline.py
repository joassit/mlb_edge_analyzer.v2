"""
Pruebas del pipeline histórico -- deterministas (usa un FakeProvider, cero
llamadas de red) e insertan HistoricalGame directamente para no depender
de ingestion.py (que sí golpea la MLB Stats API real).
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import historical_engine.db as historical_db
import historical_engine.pipeline as pipeline
from tests.test_historical_point_in_time import FakeProvider


def _fresh_historical_session(tmp_path, monkeypatch, name: str):
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db")
    historical_db.HistoricalBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(historical_db, "SessionLocal", Session)
    monkeypatch.setattr(pipeline, "SessionLocal", Session)
    return Session


def _seed_game(Session, run_id, game_pk=555, game_date="2026-05-16", season=2026,
               winner="home", away_pitcher_id=1001, home_pitcher_id=1002):
    session = Session()
    session.add(historical_db.HistoricalGame(
        run_id=run_id, game_pk=game_pk, game_date=game_date, season_year=season,
        away_team="Away Team", home_team="Home Team",
        away_team_id=111, home_team_id=147,
        away_pitcher_id=away_pitcher_id, home_pitcher_id=home_pitcher_id,
        away_pitcher_name="P1", home_pitcher_name="P2",
        status="Final" if winner else "Preview",
        home_score=5 if winner == "home" else 2, away_score=2 if winner == "home" else 5,
        winner=winner, total_runs=7,
    ))
    session.commit()
    session.close()


def test_run_over_games_creates_analysis_and_predictions_for_each_source(tmp_path, monkeypatch):
    Session = _fresh_historical_session(tmp_path, monkeypatch, "pipeline_basic")
    run_id = 1
    session = Session()
    session.add(historical_db.HistoricalRun(id=run_id, run_type="game", scope_description="test", season_year=2026))
    session.commit()
    session.close()

    _seed_game(Session, run_id)

    result = pipeline._run_over_games(run_id, season=2026, provider=FakeProvider())

    assert result.n_games == 1
    assert result.n_analyzed == 1
    assert result.n_errors == 0

    session = Session()
    analyses = session.query(historical_db.HistoricalAnalysis).filter_by(run_id=run_id).all()
    predictions = session.query(historical_db.HistoricalPrediction).filter_by(run_id=run_id).all()
    session.close()

    assert len(analyses) == 1
    assert analyses[0].away_era == 3.00  # viene del FakeProvider, valor pre-corte
    assert analyses[0].away_innings_pitched == 50.0  # IP real persistida (no aproximada)
    assert analyses[0].away_team_pa == 4200  # PA real persistida (no aproximada)
    assert len(predictions) == 3  # heuristic, skellam, negbin
    sources = {p.source for p in predictions}
    assert sources == {"heuristic", "skellam", "negbin"}


def test_predictions_mark_correct_against_actual_winner(tmp_path, monkeypatch):
    Session = _fresh_historical_session(tmp_path, monkeypatch, "pipeline_correctness")
    run_id = 1
    session = Session()
    session.add(historical_db.HistoricalRun(id=run_id, run_type="game", scope_description="test", season_year=2026))
    session.commit()
    session.close()

    _seed_game(Session, run_id, winner="home")
    pipeline._run_over_games(run_id, season=2026, provider=FakeProvider())

    session = Session()
    predictions = session.query(historical_db.HistoricalPrediction).filter_by(run_id=run_id).all()
    session.close()

    for p in predictions:
        assert p.actual_winner == "home"
        assert p.correct == (p.predicted_winner == "home")


def test_skips_game_without_confirmed_pitchers(tmp_path, monkeypatch):
    Session = _fresh_historical_session(tmp_path, monkeypatch, "pipeline_no_pitcher")
    run_id = 1
    session = Session()
    session.add(historical_db.HistoricalRun(id=run_id, run_type="game", scope_description="test", season_year=2026))
    session.commit()
    session.close()

    _seed_game(Session, run_id, away_pitcher_id=None)

    result = pipeline._run_over_games(run_id, season=2026, provider=FakeProvider())

    assert result.n_games == 1
    assert result.n_analyzed == 0
    assert result.n_skipped_missing_pitcher == 1

    session = Session()
    assert session.query(historical_db.HistoricalAnalysis).filter_by(run_id=run_id).count() == 0
    session.close()


def test_pending_game_without_result_still_analyzed_but_correct_is_none(tmp_path, monkeypatch):
    Session = _fresh_historical_session(tmp_path, monkeypatch, "pipeline_pending")
    run_id = 1
    session = Session()
    session.add(historical_db.HistoricalRun(id=run_id, run_type="game", scope_description="test", season_year=2026))
    session.commit()
    session.close()

    _seed_game(Session, run_id, winner=None)
    result = pipeline._run_over_games(run_id, season=2026, provider=FakeProvider())

    assert result.n_analyzed == 1
    session = Session()
    predictions = session.query(historical_db.HistoricalPrediction).filter_by(run_id=run_id).all()
    session.close()
    for p in predictions:
        assert p.actual_winner is None
        assert p.correct is None


def test_run_marked_completed_with_run_metadata(tmp_path, monkeypatch):
    Session = _fresh_historical_session(tmp_path, monkeypatch, "pipeline_run_meta")
    run_id = 1
    session = Session()
    session.add(historical_db.HistoricalRun(id=run_id, run_type="game", scope_description="test", season_year=2026))
    session.commit()
    session.close()

    _seed_game(Session, run_id)
    pipeline._run_over_games(run_id, season=2026, provider=FakeProvider())

    session = Session()
    run = session.query(historical_db.HistoricalRun).filter_by(id=run_id).first()
    session.close()
    assert run.status == "completed"
    assert run.n_games_processed == 1
    assert run.completed_at is not None
