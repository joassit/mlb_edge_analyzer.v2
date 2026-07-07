"""
Pruebas de tracking.results_tracker.compute_calibration() -- calibración
por bucket de confianza (max(prob, 1-prob)) para los 3 modelos
(heuristic/skellam/negbin) de _MODEL_FIELDS.
"""

from datetime import date

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


def test_home_favorite_that_wins_counts_as_hit(tmp_path, monkeypatch):
    Session = _fresh_session(tmp_path, monkeypatch, "cal_home_hit")
    session = Session()
    session.add(database.GameAnalysis(
        game_pk=1, game_date=date.today().isoformat(), away_team="A", home_team="B",
        away_model_prob=0.4, home_model_prob=0.6,
    ))
    session.add(database.ActualResult(
        game_pk=1, game_date=date.today().isoformat(), home_score=5, away_score=2, winner="home", total_runs=7,
    ))
    session.commit()
    session.close()

    cal = results_tracker.compute_calibration(days=90)
    bucket = next(b for b in cal["heuristic"]["buckets"] if b["label"] == "60-65%")

    assert bucket["n"] == 1
    assert bucket["hits"] == 1
    assert bucket["hit_rate"] == 1.0


def test_away_favorite_uses_complementary_confidence(tmp_path, monkeypatch):
    Session = _fresh_session(tmp_path, monkeypatch, "cal_away_fav")
    session = Session()
    # home_model_prob=0.3 -> visitante es favorito con confianza 0.7, y gana.
    session.add(database.GameAnalysis(
        game_pk=1, game_date=date.today().isoformat(), away_team="A", home_team="B",
        away_model_prob=0.7, home_model_prob=0.3,
    ))
    session.add(database.ActualResult(
        game_pk=1, game_date=date.today().isoformat(), home_score=1, away_score=4, winner="away", total_runs=5,
    ))
    session.commit()
    session.close()

    cal = results_tracker.compute_calibration(days=90)
    bucket = next(b for b in cal["heuristic"]["buckets"] if b["label"] == "70-75%")

    assert bucket["n"] == 1
    assert bucket["hits"] == 1
    assert abs(bucket["avg_confidence"] - 0.7) < 1e-9


def test_favorite_that_loses_is_not_a_hit(tmp_path, monkeypatch):
    Session = _fresh_session(tmp_path, monkeypatch, "cal_loss")
    session = Session()
    # Favorito local al 65%, pero pierde.
    session.add(database.GameAnalysis(
        game_pk=1, game_date=date.today().isoformat(), away_team="A", home_team="B",
        away_model_prob=0.35, home_model_prob=0.65,
    ))
    session.add(database.ActualResult(
        game_pk=1, game_date=date.today().isoformat(), home_score=1, away_score=3, winner="away", total_runs=4,
    ))
    session.commit()
    session.close()

    cal = results_tracker.compute_calibration(days=90)
    bucket = next(b for b in cal["heuristic"]["buckets"] if b["label"] == "65-70%")

    assert bucket["n"] == 1
    assert bucket["hits"] == 0
    assert bucket["hit_rate"] == 0.0


def test_gap_calculation_known_case(tmp_path, monkeypatch):
    # 2 juegos al 60% declarado, gana 1 -> hit_rate 50%, gap -10%.
    Session = _fresh_session(tmp_path, monkeypatch, "cal_gap")
    session = Session()
    session.add(database.GameAnalysis(
        game_pk=1, game_date=date.today().isoformat(), away_team="A", home_team="B",
        away_model_prob=0.4, home_model_prob=0.6,
    ))
    session.add(database.ActualResult(
        game_pk=1, game_date=date.today().isoformat(), home_score=5, away_score=2, winner="home", total_runs=7,
    ))
    session.add(database.GameAnalysis(
        game_pk=2, game_date=date.today().isoformat(), away_team="C", home_team="D",
        away_model_prob=0.4, home_model_prob=0.6,
    ))
    session.add(database.ActualResult(
        game_pk=2, game_date=date.today().isoformat(), home_score=1, away_score=3, winner="away", total_runs=4,
    ))
    session.commit()
    session.close()

    cal = results_tracker.compute_calibration(days=90)
    bucket = next(b for b in cal["heuristic"]["buckets"] if b["label"] == "60-65%")

    assert bucket["n"] == 2
    assert bucket["hits"] == 1
    assert abs(bucket["hit_rate"] - 0.5) < 1e-9
    assert abs(bucket["avg_confidence"] - 0.6) < 1e-9
    assert abs(bucket["gap"] - (-0.1)) < 1e-9


def test_empty_buckets_return_none_without_division_by_zero(tmp_path, monkeypatch):
    _fresh_session(tmp_path, monkeypatch, "cal_empty")

    cal = results_tracker.compute_calibration(days=90)

    for model_cal in cal.values():
        assert model_cal["n_games"] == 0
        for bucket in model_cal["buckets"]:
            assert bucket["n"] == 0
            assert bucket["hit_rate"] is None
            assert bucket["avg_confidence"] is None
            assert bucket["gap"] is None


def test_rows_missing_a_models_probability_are_skipped_not_broken(tmp_path, monkeypatch):
    Session = _fresh_session(tmp_path, monkeypatch, "cal_skip")
    session = Session()
    # Predicción vieja: tiene heurístico y Skellam, pero NO negbin
    # (guardada antes de la migración v08) -- no debe romper el cálculo.
    session.add(database.GameAnalysis(
        game_pk=1, game_date=date.today().isoformat(), away_team="A", home_team="B",
        away_model_prob=0.4, home_model_prob=0.6,
        away_skellam_prob=0.45, home_skellam_prob=0.55,
        away_negbin_prob=None, home_negbin_prob=None,
    ))
    session.add(database.ActualResult(
        game_pk=1, game_date=date.today().isoformat(), home_score=5, away_score=2, winner="home", total_runs=7,
    ))
    session.commit()
    session.close()

    cal = results_tracker.compute_calibration(days=90)

    assert cal["heuristic"]["n_games"] == 1
    assert cal["heuristic"]["n_skipped"] == 0
    assert cal["skellam"]["n_games"] == 1
    assert cal["negbin"]["n_games"] == 0
    assert cal["negbin"]["n_skipped"] == 1


def test_three_models_calibrate_independently_on_same_rows(tmp_path, monkeypatch):
    Session = _fresh_session(tmp_path, monkeypatch, "cal_independent")
    session = Session()
    session.add(database.GameAnalysis(
        game_pk=1, game_date=date.today().isoformat(), away_team="A", home_team="B",
        away_model_prob=0.4, home_model_prob=0.6,
        away_skellam_prob=0.3, home_skellam_prob=0.7,
        away_negbin_prob=0.48, home_negbin_prob=0.52,
    ))
    session.add(database.ActualResult(
        game_pk=1, game_date=date.today().isoformat(), home_score=5, away_score=2, winner="home", total_runs=7,
    ))
    session.commit()
    session.close()

    cal = results_tracker.compute_calibration(days=90)

    heuristic_bucket = next(b for b in cal["heuristic"]["buckets"] if b["label"] == "60-65%")
    skellam_bucket = next(b for b in cal["skellam"]["buckets"] if b["label"] == "70-75%")
    negbin_bucket = next(b for b in cal["negbin"]["buckets"] if b["label"] == "50-55%")

    assert heuristic_bucket["n"] == 1
    assert skellam_bucket["n"] == 1
    assert negbin_bucket["n"] == 1
    assert heuristic_bucket["hits"] == skellam_bucket["hits"] == negbin_bucket["hits"] == 1
