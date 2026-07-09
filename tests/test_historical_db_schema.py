"""
Prueba de aislamiento de esquema: historical_engine.db NUNCA debe compartir
tablas, Base, engine ni archivo de base de datos con la producción
(db/database.py).
"""

import db.database as production_db
import historical_engine.db as historical_db


def test_historical_base_is_not_production_base():
    assert historical_db.HistoricalBase is not production_db.Base


def test_historical_tables_share_no_names_with_production():
    production_tables = set(production_db.Base.metadata.tables.keys())
    historical_tables = set(historical_db.HistoricalBase.metadata.tables.keys())

    overlap = production_tables & historical_tables
    assert overlap == set(), f"Tablas compartidas entre producción e histórico: {overlap}"


def test_historical_tables_have_expected_entities():
    expected = {
        "historical_season", "historical_run", "historical_game", "historical_analysis",
        "historical_prediction", "historical_calibration", "historical_metrics", "historical_simulation",
    }
    actual = set(historical_db.HistoricalBase.metadata.tables.keys())
    assert expected <= actual


def test_historical_engine_is_not_production_engine():
    assert historical_db.engine is not production_db.engine


def test_historical_session_local_is_not_production_session_local():
    assert historical_db.SessionLocal is not production_db.SessionLocal


def test_historical_database_url_points_to_a_different_file_by_default():
    from historical_engine.config import HISTORICAL_DATABASE_URL
    from config import DATABASE_URL

    assert HISTORICAL_DATABASE_URL != DATABASE_URL
    assert "historical" in HISTORICAL_DATABASE_URL
