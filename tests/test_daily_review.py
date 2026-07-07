"""
Pruebas de tracking.results_tracker.compute_daily_review() -- la revisión
de UN día específico (partido por partido, mercado por mercado) que
alimenta la Sección 1 del reporte diario.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.database as database
import tracking.results_tracker as results_tracker


def _fresh_session(tmp_path, monkeypatch, name: str):
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db")
    database.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(results_tracker, "SessionLocal", Session)
    return Session


def test_compute_daily_review_returns_zero_games_with_no_data(tmp_path, monkeypatch):
    _fresh_session(tmp_path, monkeypatch, "review_empty")

    review = results_tracker.compute_daily_review("2026-07-05")

    assert review["review_date"] == "2026-07-05"
    assert review["n_games"] == 0
    assert review["games"] == []
    assert review["brier_score"] is None


def test_compute_daily_review_includes_moneyline_pick_outcome(tmp_path, monkeypatch):
    Session = _fresh_session(tmp_path, monkeypatch, "review_ml")

    session = Session()
    session.add(database.GameAnalysis(
        game_pk=1, game_date="2026-07-05", away_team="Tampa Bay Rays", home_team="Philadelphia Phillies",
        away_model_prob=0.4, home_model_prob=0.6,
        away_proj_runs=3.5, home_proj_runs=4.5,
    ))
    session.add(database.ActualResult(
        game_pk=1, game_date="2026-07-05", home_score=5, away_score=2, winner="home", total_runs=7,
    ))
    session.add(database.Pick(
        game_pk=1, game_date="2026-07-05", market="moneyline", selection="home",
        model_prob=0.6, forced=False, result="win", profit_unit=0.8,
    ))
    session.commit()
    session.close()

    review = results_tracker.compute_daily_review("2026-07-05")

    assert review["n_games"] == 1
    game = review["games"][0]
    assert game["away_team"] == "Tampa Bay Rays"
    assert game["home_team"] == "Philadelphia Phillies"
    assert game["actual_margin"] == 3
    assert game["actual_total"] == 7
    assert game["proj_margin"] == 1.0
    assert game["proj_total"] == 8.0

    ml_pick = game["picks"]["moneyline"]
    assert ml_pick["selection"] == "home"
    assert ml_pick["result"] == "win"
    assert ml_pick["model_prob"] == 0.6
    assert game["picks"]["run_line"] is None
    assert game["picks"]["totals"] is None

    assert review["by_market"]["moneyline"]["real"]["n_picks"] == 1
    assert review["brier_score"] is not None


def test_compute_daily_review_excludes_games_without_actual_result(tmp_path, monkeypatch):
    _fresh_session(tmp_path, monkeypatch, "review_pending")

    Session = results_tracker.SessionLocal
    session = Session()
    session.add(database.GameAnalysis(
        game_pk=2, game_date="2026-07-05", away_team="A", home_team="B",
        away_model_prob=0.5, home_model_prob=0.5,
    ))
    session.commit()
    session.close()

    review = results_tracker.compute_daily_review("2026-07-05")

    assert review["n_games"] == 0


def test_compute_daily_review_dedups_by_game_pk_keeping_latest(tmp_path, monkeypatch):
    _fresh_session(tmp_path, monkeypatch, "review_dedup")

    Session = results_tracker.SessionLocal
    session = Session()
    session.add(database.GameAnalysis(
        game_pk=3, game_date="2026-07-05", away_team="A", home_team="B",
        away_model_prob=0.5, home_model_prob=0.5, model_version="v1",
    ))
    session.add(database.GameAnalysis(
        game_pk=3, game_date="2026-07-05", away_team="A", home_team="B",
        away_model_prob=0.3, home_model_prob=0.7, model_version="v2",
    ))
    session.add(database.ActualResult(
        game_pk=3, game_date="2026-07-05", home_score=4, away_score=1, winner="home", total_runs=5,
    ))
    session.commit()
    session.close()

    review = results_tracker.compute_daily_review("2026-07-05")

    assert review["n_games"] == 1


def test_compute_daily_review_excludes_rows_failing_validation(tmp_path, monkeypatch):
    _fresh_session(tmp_path, monkeypatch, "review_invalid")

    Session = results_tracker.SessionLocal
    session = Session()
    # Probabilidades que no suman 1 -- fila corrupta, debe excluirse.
    session.add(database.GameAnalysis(
        game_pk=4, game_date="2026-07-05", away_team="A", home_team="B",
        away_model_prob=0.6, home_model_prob=0.6,
    ))
    session.add(database.ActualResult(
        game_pk=4, game_date="2026-07-05", home_score=3, away_score=1, winner="home", total_runs=4,
    ))
    session.commit()
    session.close()

    review = results_tracker.compute_daily_review("2026-07-05")

    assert review["n_games"] == 0


def test_compute_daily_review_by_market_dedupes_recalculated_pick(tmp_path, monkeypatch):
    # Mismo bug que en compute_pick_performance(): un recálculo intradía
    # que cambia `selection` no choca con uq_pick_game_market_selection
    # (selection es distinto), así que ambas filas quedan en la tabla.
    # by_market no debe contar el mismo game_pk+market dos veces.
    from datetime import datetime

    _fresh_session(tmp_path, monkeypatch, "review_by_market_dedup")

    Session = results_tracker.SessionLocal
    session = Session()
    session.add(database.GameAnalysis(
        game_pk=6, game_date="2026-07-05", away_team="A", home_team="B",
        away_model_prob=0.4, home_model_prob=0.6,
    ))
    session.add(database.ActualResult(
        game_pk=6, game_date="2026-07-05", home_score=5, away_score=2, winner="home", total_runs=7,
    ))
    session.add(database.Pick(
        game_pk=6, game_date="2026-07-05", market="moneyline", selection="away",
        model_prob=0.55, forced=False, result="loss", profit_unit=-1.0,
        created_at=datetime(2026, 7, 5, 0, 42, 0),
    ))
    session.add(database.Pick(
        game_pk=6, game_date="2026-07-05", market="moneyline", selection="home",
        model_prob=0.6, forced=False, result="win", profit_unit=0.8,
        created_at=datetime(2026, 7, 5, 7, 23, 0),
    ))
    session.commit()
    session.close()

    review = results_tracker.compute_daily_review("2026-07-05")

    assert review["by_market"]["moneyline"]["real"]["n_picks"] == 1
    # El picks_by_game mostrado en la ficha del partido debe ser el más
    # reciente (el ganador), no cualquiera de los dos al azar.
    assert review["games"][0]["picks"]["moneyline"]["result"] == "win"


def test_compute_daily_review_no_pick_still_reports_projection_vs_actual(tmp_path, monkeypatch):
    _fresh_session(tmp_path, monkeypatch, "review_no_pick")

    Session = results_tracker.SessionLocal
    session = Session()
    session.add(database.GameAnalysis(
        game_pk=5, game_date="2026-07-05", away_team="A", home_team="B",
        away_model_prob=0.5, home_model_prob=0.5,
        away_proj_runs=3.0, home_proj_runs=4.0,
    ))
    session.add(database.ActualResult(
        game_pk=5, game_date="2026-07-05", home_score=6, away_score=6, winner="home", total_runs=12,
    ))
    session.commit()
    session.close()

    review = results_tracker.compute_daily_review("2026-07-05")

    assert review["n_games"] == 1
    game = review["games"][0]
    assert game["picks"]["moneyline"] is None
    assert game["proj_total"] == 7.0
    assert game["actual_total"] == 12
