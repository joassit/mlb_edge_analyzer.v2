"""
V11 — pruebas de db/migrate_v07.py: agrega el índice ix_game_date_pk
(game_date, game_pk) a bases de datos existentes creadas antes de este
índice. Cada test corre contra un SQLite temporal y descartable.
"""

import sqlite3

from sqlalchemy import create_engine, inspect

import db.migrate_v07 as migrate_v07
import db.database as database


def test_migrate_is_no_op_when_table_does_not_exist_yet(tmp_path, monkeypatch):
    db_path = tmp_path / "nodb.db"
    temp_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(migrate_v07, "engine", temp_engine)

    migrate_v07.migrate()  # no debe fallar aunque no exista la tabla

    conn = sqlite3.connect(str(db_path))
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    conn.close()
    assert "game_analysis" not in tables


def test_migrate_adds_index_to_table_missing_it(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE game_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_pk INTEGER NOT NULL,
            game_date VARCHAR NOT NULL,
            away_team VARCHAR NOT NULL,
            home_team VARCHAR NOT NULL
        )
    """)
    conn.commit()
    conn.close()

    temp_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(migrate_v07, "engine", temp_engine)

    migrate_v07.migrate()

    inspector = inspect(temp_engine)
    indexes = [ix["name"] for ix in inspector.get_indexes("game_analysis")]
    assert "ix_game_date_pk" in indexes


def test_migrate_is_a_no_op_when_index_already_exists(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    temp_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(migrate_v07, "engine", temp_engine)
    database.Base.metadata.create_all(temp_engine)  # ya con el índice nuevo

    migrate_v07.migrate()  # no debe fallar ni intentar crear el índice de nuevo

    inspector = inspect(temp_engine)
    indexes = [ix["name"] for ix in inspector.get_indexes("game_analysis")]
    assert indexes.count("ix_game_date_pk") == 1
