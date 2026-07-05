"""
Pruebas de db/database.py: idempotencia (upsert), Feature Snapshot Store, y
cálculo de CLV. Cada test corre contra un SQLite temporal y descartable —
se redirige `engine`/`SessionLocal` del módulo real, nunca se toca
mlb_edge.db ni se deja estado que contamine otros tests.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.database as database


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    temp_engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setattr(database, "engine", temp_engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=temp_engine))
    database.init_db()
    return database


def test_save_analysis_upserts_instead_of_duplicating(isolated_db):
    row = {
        "game_pk": 1, "game_date": "2026-07-05", "away_team": "A", "home_team": "B",
        "away_model_prob": 0.6, "home_model_prob": 0.4,
    }
    isolated_db.save_analysis(row)
    isolated_db.save_analysis(row)  # re-ejecución del pipeline el mismo día

    session = isolated_db.SessionLocal()
    try:
        count = session.query(isolated_db.GameAnalysis).count()
    finally:
        session.close()
    assert count == 1


def test_feature_snapshot_round_trip(isolated_db):
    raw_inputs = {"away_era": 3.5, "home_era": 4.1, "park_factor": 1.02}
    isolated_db.save_feature_snapshot(game_pk=42, game_date="2026-07-05", raw_inputs=raw_inputs)

    snap = isolated_db.get_feature_snapshot(game_pk=42, game_date="2026-07-05")

    assert snap is not None
    assert snap["raw_inputs"] == raw_inputs


def test_feature_snapshot_upserts_instead_of_duplicating(isolated_db):
    isolated_db.save_feature_snapshot(game_pk=42, game_date="2026-07-05", raw_inputs={"v": 1})
    isolated_db.save_feature_snapshot(game_pk=42, game_date="2026-07-05", raw_inputs={"v": 2})

    session = isolated_db.SessionLocal()
    try:
        count = session.query(isolated_db.FeatureSnapshot).count()
    finally:
        session.close()
    assert count == 1

    snap = isolated_db.get_feature_snapshot(game_pk=42, game_date="2026-07-05")
    assert snap["raw_inputs"] == {"v": 2}


def test_get_feature_snapshot_returns_none_when_missing(isolated_db):
    assert isolated_db.get_feature_snapshot(game_pk=999, game_date="2026-07-05") is None


def test_record_closing_odds_computes_positive_clv_when_line_moves_toward_your_side(isolated_db):
    bet_id = isolated_db.record_bet({
        "game_pk": 7, "game_date": "2026-07-05", "market": "moneyline",
        "side": "away", "odds": -135, "model_prob": 0.6, "stake": 1.0,
    })

    updated = isolated_db.record_closing_odds(game_pk=7, side="away", closing_odds=-160)
    assert updated == 1

    session = isolated_db.SessionLocal()
    try:
        bet = session.get(isolated_db.Bet, bet_id)
        # el mercado se movió a favor de tu lado (-135 -> -160): CLV positivo
        assert bet.clv > 0
        assert bet.closing_odds == -160
    finally:
        session.close()


def test_record_closing_odds_negative_when_line_moves_against_your_side(isolated_db):
    bet_id = isolated_db.record_bet({
        "game_pk": 8, "game_date": "2026-07-05", "market": "moneyline",
        "side": "home", "odds": 120, "model_prob": 0.5, "stake": 1.0,
    })

    isolated_db.record_closing_odds(game_pk=8, side="home", closing_odds=150)

    session = isolated_db.SessionLocal()
    try:
        bet = session.get(isolated_db.Bet, bet_id)
        assert bet.clv < 0
    finally:
        session.close()
