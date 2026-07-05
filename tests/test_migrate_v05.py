"""
Pruebas de db/migrate_v05.py — reconstrucción de game_analysis con el
constraint uq_pred (game_pk, game_date, model_version). Cada test corre
contra un SQLite temporal y descartable.
"""

import sqlite3

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.migrate_v05 as migrate_v05
import db.database as database


def test_migrate_creates_schema_when_no_prior_table(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    temp_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(migrate_v05, "DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(migrate_v05, "engine", temp_engine)

    migrate_v05.migrate()

    conn = sqlite3.connect(str(db_path))
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    conn.close()
    assert "game_analysis" in tables


def test_migrate_dedupes_existing_rows_and_preserves_data(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE game_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_pk INTEGER NOT NULL,
            game_date VARCHAR NOT NULL,
            away_team VARCHAR NOT NULL,
            home_team VARCHAR NOT NULL,
            home_model_prob FLOAT,
            away_model_prob FLOAT,
            model_version VARCHAR,
            UNIQUE(game_pk, game_date)
        )
    """)
    conn.execute(
        "INSERT INTO game_analysis (game_pk, game_date, away_team, home_team, "
        "home_model_prob, away_model_prob, model_version) "
        "VALUES (1, '2026-07-01', 'A', 'B', 0.4, 0.6, 'v1')"
    )
    conn.commit()
    conn.close()

    temp_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(migrate_v05, "DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(migrate_v05, "engine", temp_engine)

    migrate_v05.migrate()

    session = sessionmaker(bind=temp_engine)()
    try:
        rows = session.query(database.GameAnalysis).all()
    finally:
        session.close()

    assert len(rows) == 1
    assert rows[0].game_pk == 1
    assert rows[0].model_version == "v1"
    assert rows[0].home_model_prob == 0.4

    conn = sqlite3.connect(str(db_path))
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    conn.close()
    assert "game_analysis_old" not in tables  # se limpió al final


def test_migrate_keeps_most_recent_row_per_key_when_duplicated(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    # Esquema viejo SIN constraint (peor caso: pudo haber quedado con
    # duplicados reales antes de que existiera cualquier UniqueConstraint).
    conn.execute("""
        CREATE TABLE game_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_pk INTEGER NOT NULL,
            game_date VARCHAR NOT NULL,
            away_team VARCHAR NOT NULL,
            home_team VARCHAR NOT NULL,
            home_model_prob FLOAT,
            away_model_prob FLOAT,
            model_version VARCHAR
        )
    """)
    conn.execute(
        "INSERT INTO game_analysis (game_pk, game_date, away_team, home_team, "
        "home_model_prob, away_model_prob, model_version) "
        "VALUES (1, '2026-07-01', 'A', 'B', 0.40, 0.60, 'v1')"
    )
    conn.execute(
        "INSERT INTO game_analysis (game_pk, game_date, away_team, home_team, "
        "home_model_prob, away_model_prob, model_version) "
        "VALUES (1, '2026-07-01', 'A', 'B', 0.55, 0.45, 'v1')"  # duplicado real, más reciente
    )
    conn.commit()
    conn.close()

    temp_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(migrate_v05, "DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(migrate_v05, "engine", temp_engine)

    migrate_v05.migrate()

    session = sessionmaker(bind=temp_engine)()
    try:
        rows = session.query(database.GameAnalysis).all()
    finally:
        session.close()

    assert len(rows) == 1
    assert rows[0].home_model_prob == 0.55  # se quedó con la fila de mayor id


def test_migrate_is_a_no_op_when_constraint_already_exists(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    temp_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(migrate_v05, "DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(migrate_v05, "engine", temp_engine)
    database.Base.metadata.create_all(temp_engine)  # ya con el esquema/constraint nuevo

    migrate_v05.migrate()  # no debe fallar ni reconstruir nada

    conn = sqlite3.connect(str(db_path))
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    conn.close()
    assert "game_analysis_old" not in tables
