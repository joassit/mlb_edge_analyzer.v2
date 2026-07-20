"""
Pruebas de scripts/migrate_sqlite_to_postgres.py -- nunca toca un
mlb_edge.db real ni un Postgres real. El destino se simula con SQLite
(la lógica de copia de filas vía SQLAlchemy Core es la misma sin
importar el dialecto) -- solo se prueba la lógica de migración/skip, no
la conexión real a Postgres.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import db.database as database
import scripts.migrate_sqlite_to_postgres as migrate_script


def _make_sqlite_source(tmp_path, filename="source.db") -> str:
    path = tmp_path / filename
    engine = create_engine(f"sqlite:///{path}")
    database.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add(database.ActualResult(
        game_pk=555, game_date="2026-07-06", home_score=5, away_score=2, winner="home", total_runs=7,
    ))
    session.add(database.Pick(
        game_pk=555, game_date="2026-07-06", market="moneyline", selection="home",
        model_prob=0.55, prob_source="heuristic", model_version="v1.0.0",
    ))
    session.commit()
    session.close()
    return str(path)


def test_migrate_copies_all_rows_to_empty_destination(tmp_path, monkeypatch):
    source_path = _make_sqlite_source(tmp_path)
    dest_engine = create_engine(f"sqlite:///{tmp_path}/dest.db")
    monkeypatch.setattr(database, "engine", dest_engine)

    result = migrate_script.migrate(sqlite_path=source_path)

    assert result["actual_results"] == 1
    assert result["picks"] == 1

    DestSession = sessionmaker(bind=dest_engine)
    dest_session = DestSession()
    try:
        assert dest_session.query(database.ActualResult).count() == 1
        assert dest_session.query(database.Pick).count() == 1
        pick = dest_session.query(database.Pick).one()
        assert pick.model_prob == 0.55
        assert pick.prob_source == "heuristic"
    finally:
        dest_session.close()


def test_migrate_skips_tables_that_already_have_rows_in_destination(tmp_path, monkeypatch):
    source_path = _make_sqlite_source(tmp_path)
    dest_engine = create_engine(f"sqlite:///{tmp_path}/dest2.db")
    database.Base.metadata.create_all(dest_engine)
    DestSession = sessionmaker(bind=dest_engine)
    dest_session = DestSession()
    dest_session.add(database.ActualResult(
        game_pk=999, game_date="2026-01-01", home_score=1, away_score=0, winner="home", total_runs=1,
    ))
    dest_session.commit()
    dest_session.close()

    monkeypatch.setattr(database, "engine", dest_engine)
    result = migrate_script.migrate(sqlite_path=source_path)

    # actual_results ya tenia 1 fila -- se salta, nunca se agrega la del source.
    assert result["actual_results"] == 0
    dest_session = DestSession()
    try:
        assert dest_session.query(database.ActualResult).count() == 1
        assert dest_session.query(database.ActualResult).one().game_pk == 999
        # picks SI estaba vacia -- si se migra normalmente.
        assert dest_session.query(database.Pick).count() == 1
    finally:
        dest_session.close()


def test_migrate_fills_not_null_columns_with_null_in_source_using_declared_default(tmp_path, monkeypatch):
    """Escenario real (run 29699868482 contra Neon): filas viejas de
    `picks` en SQLite tienen `calibration_phase=NULL` a pesar de que el
    modelo lo declara `nullable=False, default=False` -- SQLite nunca
    reforzo esa restriccion retroactivamente. Postgres si la exige
    (NotNullViolation real). El fix debe rellenar con el mismo default
    que usaria el ORM, nunca dejar pasar el NULL crudo.

    El origen real de la fila NULL es `_auto_add_missing_columns()`
    (db/database.py): agrega columnas nuevas del modelo a una tabla YA
    existente via `ALTER TABLE ... ADD COLUMN` crudo, sin clausula NOT
    NULL, aunque el modelo Python declare `nullable=False`. Por eso el
    fixture reproduce ese mismo historial en vez de usar
    `Base.metadata.create_all()` para la tabla `picks` -- create_all()
    SI aplicaria la constraint NOT NULL desde cero (a diferencia de un
    ALTER TABLE sobre una tabla vieja) y no dejaria insertar el NULL que
    este test necesita para reproducir el bug real."""
    source_path = tmp_path / "source_with_null.db"
    source_engine = create_engine(f"sqlite:///{source_path}")
    other_tables = [t for t in database.Base.metadata.sorted_tables if t.name != "picks"]
    database.Base.metadata.create_all(source_engine, tables=other_tables)
    with source_engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE picks ("
            "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
            "game_pk INTEGER NOT NULL, "
            "game_date VARCHAR NOT NULL, "
            "market VARCHAR NOT NULL, "
            "selection VARCHAR NOT NULL, "
            "line FLOAT, "
            "favorite_side VARCHAR, "
            "model_prob FLOAT NOT NULL, "
            "market_prob FLOAT, "
            "edge FLOAT, "
            "ev FLOAT, "
            "odds_used FLOAT, "
            "prob_source VARCHAR, "
            "directional_discrepancy BOOLEAN, "
            "result VARCHAR, "
            "profit_unit FLOAT, "
            "model_version VARCHAR, "
            "created_at DATETIME"
            ")"
        ))
        # Historial real: estas dos columnas se agregaron DESPUES via el
        # ALTER TABLE permisivo de _auto_add_missing_columns() -- nunca
        # quedan declaradas NOT NULL a nivel de esquema real, aunque el
        # modelo lo diga.
        conn.execute(text("ALTER TABLE picks ADD COLUMN forced BOOLEAN"))
        conn.execute(text("ALTER TABLE picks ADD COLUMN calibration_phase BOOLEAN"))
        conn.execute(text(
            "INSERT INTO picks (game_pk, game_date, market, selection, model_prob, model_version, "
            "forced, calibration_phase) VALUES "
            "(1, '2026-01-01', 'moneyline', 'home', 0.5, 'v1', NULL, NULL)"
        ))

    dest_engine = create_engine(f"sqlite:///{tmp_path}/dest_null.db")
    monkeypatch.setattr(database, "engine", dest_engine)

    result = migrate_script.migrate(sqlite_path=str(source_path))
    assert result["picks"] == 1

    DestSession = sessionmaker(bind=dest_engine)
    dest_session = DestSession()
    try:
        pick = dest_session.query(database.Pick).one()
        assert pick.forced is False
        assert pick.calibration_phase is False
    finally:
        dest_session.close()


class _FakeDialect:
    def __init__(self, name):
        self.name = name


class _FakeConnection:
    """Stub minimo -- _reset_autoincrement_sequence() solo lee conn.dialect.name
    y llama conn.execute(text(...)), nunca hace falta un motor real (ni
    SQLite ni Postgres) para probar el branching por dialecto."""
    def __init__(self, dialect_name):
        self.dialect = _FakeDialect(dialect_name)
        self.executed = []

    def execute(self, stmt):
        self.executed.append(str(stmt))


def test_reset_autoincrement_sequence_noop_for_non_postgres_dialect():
    # Escenario real: los tests de este archivo migran contra SQLite (nunca
    # un Postgres real) -- setval() no existe en SQLite, así que la función
    # debe ser un no-op silencioso ahí, nunca intentar ejecutar SQL de Postgres.
    conn = _FakeConnection("sqlite")
    migrate_script._reset_autoincrement_sequence(conn, database.GameAnalysis.__table__)
    assert conn.executed == []


def test_reset_autoincrement_sequence_noop_for_table_without_autoincrement_id():
    # ActualResult usa game_pk como PK (sin columna `id` autoincremental)
    # -- no hay ninguna secuencia que resincronizar, incluso contra Postgres.
    conn = _FakeConnection("postgresql")
    migrate_script._reset_autoincrement_sequence(conn, database.ActualResult.__table__)
    assert conn.executed == []


def test_reset_autoincrement_sequence_calls_setval_for_postgres_table_with_id():
    # Caso real que rompió la corrida del 2026-07-20: game_analysis SI tiene
    # `id` autoincremental -- contra Postgres, debe resincronizar la
    # secuencia con setval()/pg_get_serial_sequence() para que el próximo
    # INSERT sin id explícito (el próximo GameAnalysis del pipeline diario)
    # no choque con un id que la migración ya usó.
    conn = _FakeConnection("postgresql")
    migrate_script._reset_autoincrement_sequence(conn, database.GameAnalysis.__table__)
    assert len(conn.executed) == 1
    assert "setval" in conn.executed[0]
    assert "pg_get_serial_sequence" in conn.executed[0]
    assert "game_analysis" in conn.executed[0]


def test_migrate_is_a_noop_on_empty_source(tmp_path, monkeypatch):
    source_path = tmp_path / "empty_source.db"
    source_engine = create_engine(f"sqlite:///{source_path}")
    database.Base.metadata.create_all(source_engine)

    dest_engine = create_engine(f"sqlite:///{tmp_path}/dest3.db")
    monkeypatch.setattr(database, "engine", dest_engine)

    result = migrate_script.migrate(sqlite_path=str(source_path))
    assert all(v == 0 for v in result.values())
