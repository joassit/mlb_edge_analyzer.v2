"""
Pruebas de scripts/check_already_ran_today.py -- la guarda de
idempotencia del cron de respaldo (daily_pipeline.yml). Nunca toca un
mlb_edge.db real.
"""

from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.database as database
import scripts.check_already_ran_today as check_script


def _isolated_db(tmp_path, monkeypatch):
    """No llama a init_db() -- a propósito, para que
    test_already_ran_today_is_false_on_a_fresh_db_without_any_tables
    pueda probar que already_ran_today() crea las tablas ella misma.
    Los demás tests llaman a database.init_db() explícitamente antes de
    usar save_analysis()."""
    temp_engine = create_engine(f"sqlite:///{tmp_path}/check_test.db")
    monkeypatch.setattr(database, "engine", temp_engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=temp_engine))
    return database


def test_already_ran_today_is_false_on_a_fresh_db_without_any_tables(tmp_path, monkeypatch):
    # Caché fría / primer uso: ni siquiera existe la tabla game_analysis
    # todavía -- init_db() dentro de already_ran_today() debe crearla,
    # no reventar con "no such table".
    _isolated_db(tmp_path, monkeypatch)
    assert check_script.already_ran_today() is False


def test_already_ran_today_is_true_when_a_row_exists_for_today(tmp_path, monkeypatch):
    db_mod = _isolated_db(tmp_path, monkeypatch)
    db_mod.init_db()
    today = date.today().strftime("%Y-%m-%d")
    db_mod.save_analysis({
        "game_pk": 1, "game_date": today, "away_team": "A", "home_team": "B",
        "away_model_prob": 0.6, "home_model_prob": 0.4,
    })

    assert check_script.already_ran_today() is True


def test_already_ran_today_ignores_rows_from_other_dates(tmp_path, monkeypatch):
    db_mod = _isolated_db(tmp_path, monkeypatch)
    db_mod.init_db()
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    db_mod.save_analysis({
        "game_pk": 1, "game_date": yesterday, "away_team": "A", "home_team": "B",
        "away_model_prob": 0.6, "home_model_prob": 0.4,
    })

    # Solo hay una fila, y es de AYER -- hoy sigue sin ninguna predicción.
    assert check_script.already_ran_today() is False
