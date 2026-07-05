"""
Pruebas de tracking/backtest.py contra un SQLite temporal y aislado —
verifica tanto el caso "sin suficiente data" como el recálculo real desde
snapshots congelados.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.database as database
import tracking.backtest as backtest_module
from tests.test_predictor import _base_raw_inputs


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    temp_engine = create_engine(f"sqlite:///{tmp_path}/backtest_test.db")
    monkeypatch.setattr(database, "engine", temp_engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=temp_engine))
    monkeypatch.setattr(backtest_module, "SessionLocal", database.SessionLocal)
    database.init_db()
    return database


def test_walk_forward_backtest_reports_insufficient_data(isolated_db):
    result = backtest_module.walk_forward_backtest(min_games=10)

    assert result["n_games"] == 0
    assert result["brier_score"] is None
    assert "Se necesitan al menos" in result["message"]


def test_walk_forward_backtest_recomputes_from_frozen_snapshots(isolated_db):
    # Dos snapshots congelados: uno donde el visitante es claramente mejor
    # (y gana en la realidad), otro con los roles invertidos.
    isolated_db.save_feature_snapshot(1, "2026-06-01", _base_raw_inputs())
    isolated_db.save_result({
        "game_pk": 1, "game_date": "2026-06-01",
        "home_score": 2, "away_score": 5, "winner": "away", "total_runs": 7,
    })

    reversed_inputs = _base_raw_inputs(
        away_era=4.50, home_era=3.00, away_ops=0.740, home_ops=0.780,
    )
    isolated_db.save_feature_snapshot(2, "2026-06-02", reversed_inputs)
    isolated_db.save_result({
        "game_pk": 2, "game_date": "2026-06-02",
        "home_score": 5, "away_score": 2, "winner": "home", "total_runs": 7,
    })

    result = backtest_module.walk_forward_backtest(min_games=2)

    assert result["n_games"] == 2
    assert result["message"] is None
    assert result["brier_score"] is not None
    # El modelo favoreció al ganador real en ambos casos -> Brier bajo (< 0.25)
    assert result["brier_score"] < 0.25
    assert len(result["per_game"]) == 2
