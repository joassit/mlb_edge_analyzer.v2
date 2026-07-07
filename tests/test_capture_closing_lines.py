"""
Pruebas de scripts/capture_closing_lines.py — mockea schedule/odds/DB,
nunca toca la red ni un mlb_edge.db real.
"""

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.database as database
import scripts.capture_closing_lines as capture_script


def _isolated_db(tmp_path, monkeypatch):
    temp_engine = create_engine(f"sqlite:///{tmp_path}/capture_test.db")
    monkeypatch.setattr(database, "engine", temp_engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=temp_engine))
    monkeypatch.setattr(capture_script, "get_pending_moneyline_bets", database.get_pending_moneyline_bets)
    monkeypatch.setattr(capture_script, "record_closing_odds", database.record_closing_odds)
    monkeypatch.setattr(capture_script, "init_db", database.init_db)
    database.init_db()
    return database


def test_capture_closing_lines_records_odds_for_matched_pending_bets(tmp_path, monkeypatch):
    db_mod = _isolated_db(tmp_path, monkeypatch)
    today = date.today().strftime("%Y-%m-%d")

    bet_id = db_mod.record_bet({
        "game_pk": 42, "game_date": today, "market": "moneyline",
        "side": "away", "odds": -135, "model_prob": 0.6, "stake": 1.0,
    })

    monkeypatch.setattr(capture_script, "get_schedule", lambda d: [{
        "game_pk": 42, "away_team": "Away Team", "home_team": "Home Team",
    }])
    fake_event = {
        "away_team": "Away Team", "home_team": "Home Team", "commence_time": None,
        "prices": [{"book": "fakebook", "away_price": -160, "home_price": 140, "last_update": None}],
    }
    monkeypatch.setattr(capture_script, "fetch_moneyline_odds", lambda: [fake_event])

    updated = capture_script.capture_closing_lines()

    assert updated == 1
    session = db_mod.SessionLocal()
    try:
        bet = session.get(db_mod.Bet, bet_id)
        assert bet.closing_odds == -160
        assert bet.clv is not None
    finally:
        session.close()


def test_capture_closing_lines_returns_zero_without_pending_bets(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    monkeypatch.setattr(capture_script, "get_schedule", lambda d: [])
    monkeypatch.setattr(capture_script, "fetch_moneyline_odds", lambda: [])

    assert capture_script.capture_closing_lines() == 0
