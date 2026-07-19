"""
Pruebas de scripts/migrate_sqlite_to_postgres.py -- nunca toca un
mlb_edge.db real ni un Postgres real. El destino se simula con SQLite
(la lógica de copia de filas vía SQLAlchemy Core es la misma sin
importar el dialecto) -- solo se prueba la lógica de migración/skip, no
la conexión real a Postgres.
"""

from sqlalchemy import create_engine
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


def test_migrate_is_a_noop_on_empty_source(tmp_path, monkeypatch):
    source_path = tmp_path / "empty_source.db"
    source_engine = create_engine(f"sqlite:///{source_path}")
    database.Base.metadata.create_all(source_engine)

    dest_engine = create_engine(f"sqlite:///{tmp_path}/dest3.db")
    monkeypatch.setattr(database, "engine", dest_engine)

    result = migrate_script.migrate(sqlite_path=str(source_path))
    assert all(v == 0 for v in result.values())
