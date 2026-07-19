"""`cross_model/db.py` -- schema, upsert idempotente, y la consulta de
ejemplo que demuestra "cruzar resultados con SQL directo" entre sistemas.
SQLite real basado en archivo (nunca `:memory:`, mismo criterio que
`jsa/historical/`)."""

from __future__ import annotations

import datetime

import pytest

from cross_model import db as unified_db


@pytest.fixture()
def engine(tmp_path):
    eng = unified_db.get_engine(f"sqlite:///{tmp_path}/unified_test.db")
    unified_db.init_storage(eng)
    return eng


def test_upsert_prediction_then_read_back(engine):
    unified_db.upsert_prediction(
        engine, game_pk=1001, game_date=datetime.date(2024, 4, 1), season=2024,
        system="jsa", model_name="evidence_score_raw", model_version="v1",
        raw_score=0.42, home_win_prob=None, predicted_winner="home", actual_winner="home",
        source_ref="test",
    )
    rows = unified_db.predictions_for_game(engine, 1001)
    assert len(rows) == 1
    assert rows[0]["predicted_winner"] == "home"
    assert rows[0]["actual_winner"] == "home"
    assert rows[0]["correct"] is True


def test_upsert_is_idempotent_and_updates_existing_row(engine):
    unified_db.upsert_prediction(
        engine, game_pk=1002, game_date=datetime.date(2024, 4, 1), season=2024,
        system="game_flow", model_name="gf1_starter_durability", model_version="v1",
        raw_score=-0.1, home_win_prob=None, predicted_winner="away", actual_winner=None,
        source_ref="test",
    )
    # El juego termina despues -- se re-corre el sync con actual_winner ya conocido.
    unified_db.upsert_prediction(
        engine, game_pk=1002, game_date=datetime.date(2024, 4, 1), season=2024,
        system="game_flow", model_name="gf1_starter_durability", model_version="v1",
        raw_score=-0.1, home_win_prob=None, predicted_winner="away", actual_winner="away",
        source_ref="test",
    )
    rows = unified_db.predictions_for_game(engine, 1002)
    assert len(rows) == 1  # nunca duplica -- mismo (game_pk, system, model_name, model_version)
    assert rows[0]["actual_winner"] == "away"
    assert rows[0]["correct"] is True


def test_correct_is_none_when_actual_winner_unknown(engine):
    unified_db.upsert_prediction(
        engine, game_pk=1003, game_date=datetime.date(2024, 4, 1), season=2024,
        system="jsa", model_name="evidence_score_raw", model_version="v1",
        raw_score=0.1, home_win_prob=None, predicted_winner="home", actual_winner=None,
        source_ref="test",
    )
    rows = unified_db.predictions_for_game(engine, 1003)
    assert rows[0]["correct"] is None


def test_accuracy_by_system_and_model_crosses_systems_with_sql(engine):
    """La demostracion concreta de "cruzar resultados con SQL directo":
    2 sistemas distintos, cada uno con su propia precision, calculada en
    UNA sola consulta sobre la tabla compartida."""
    # jsa: 2/2 correctas.
    for pk, pred, actual in ((2001, "home", "home"), (2002, "away", "away")):
        unified_db.upsert_prediction(
            engine, game_pk=pk, game_date=datetime.date(2024, 4, 1), season=2024,
            system="jsa", model_name="evidence_score_raw", model_version="v1",
            raw_score=0.1, home_win_prob=None, predicted_winner=pred, actual_winner=actual, source_ref="test",
        )
    # game_flow: 1/2 correctas.
    for pk, pred, actual in ((2001, "home", "home"), (2002, "home", "away")):
        unified_db.upsert_prediction(
            engine, game_pk=pk, game_date=datetime.date(2024, 4, 1), season=2024,
            system="game_flow", model_name="gf1_starter_durability", model_version="v1",
            raw_score=0.1, home_win_prob=None, predicted_winner=pred, actual_winner=actual, source_ref="test",
        )

    result = unified_db.accuracy_by_system_and_model(engine)
    by_system = {(r["system"], r["model_name"]): r["accuracy"] for r in result}
    assert by_system[("jsa", "evidence_score_raw")] == 1.0
    assert by_system[("game_flow", "gf1_starter_durability")] == 0.5


def test_accuracy_by_system_and_model_filters_by_season(engine):
    unified_db.upsert_prediction(
        engine, game_pk=3001, game_date=datetime.date(2023, 4, 1), season=2023,
        system="jsa", model_name="evidence_score_raw", model_version="v1",
        raw_score=0.1, home_win_prob=None, predicted_winner="home", actual_winner="away", source_ref="test",
    )
    unified_db.upsert_prediction(
        engine, game_pk=3002, game_date=datetime.date(2024, 4, 1), season=2024,
        system="jsa", model_name="evidence_score_raw", model_version="v1",
        raw_score=0.1, home_win_prob=None, predicted_winner="home", actual_winner="home", source_ref="test",
    )
    result = unified_db.accuracy_by_system_and_model(engine, seasons=[2024])
    assert len(result) == 1
    assert result[0]["n_games"] == 1
    assert result[0]["accuracy"] == 1.0
