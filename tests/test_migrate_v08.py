"""
Pruebas de db/migrate_v08.py: agrega away_negbin_prob / home_negbin_prob a
game_analysis en bases de datos existentes creadas antes de este cambio.
Cada test corre contra un SQLite temporal y descartable.
"""

import sqlite3

from sqlalchemy import create_engine, inspect

import db.migrate_v08 as migrate_v08
import db.database as database


def test_migrate_is_no_op_when_table_does_not_exist_yet(tmp_path, monkeypatch):
    db_path = tmp_path / "nodb.db"
    temp_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(migrate_v08, "engine", temp_engine)

    migrate_v08.migrate()  # no debe fallar aunque no exista la tabla

    conn = sqlite3.connect(str(db_path))
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    conn.close()
    assert "game_analysis" not in tables


def test_migrate_adds_missing_columns_to_table_created_before_negbin(tmp_path, monkeypatch):
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
    monkeypatch.setattr(migrate_v08, "engine", temp_engine)

    migrate_v08.migrate()

    inspector = inspect(temp_engine)
    columns = {c["name"] for c in inspector.get_columns("game_analysis")}
    assert "away_negbin_prob" in columns
    assert "home_negbin_prob" in columns


def test_migrate_is_a_no_op_when_columns_already_exist(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    temp_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(migrate_v08, "engine", temp_engine)
    database.Base.metadata.create_all(temp_engine)  # ya con las columnas nuevas

    migrate_v08.migrate()  # no debe fallar ni intentar agregar las columnas de nuevo

    inspector = inspect(temp_engine)
    columns = [c["name"] for c in inspector.get_columns("game_analysis")]
    assert columns.count("away_negbin_prob") == 1
