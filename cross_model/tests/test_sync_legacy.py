"""`cross_model/sync_legacy.py` -- sincroniza picks moneyline del modelo
MLB legado desde una base de produccion sintetica (mismo patron que
`tests/test_historical_isolation.py::_seed_production_db()`) hacia
`unified_model_predictions`. Nunca llama a ninguna funcion de escritura
de `db.database` -- solo lee, con su propio engine/sesion. SQLite real
basado en archivo."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.database as production_db

from cross_model import db as unified_db
from cross_model import sync_legacy


def _seed_legacy_db(db_path) -> str:
    url = f"sqlite:///{db_path}"
    engine = create_engine(url, future=True)
    production_db.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    session.add(production_db.ActualResult(
        game_pk=555, game_date="2024-07-06", home_score=5, away_score=2, winner="home", total_runs=7,
    ))
    session.add(production_db.ActualResult(
        game_pk=556, game_date="2024-07-06", home_score=3, away_score=6, winner="away", total_runs=9,
    ))
    session.add(production_db.Pick(
        game_pk=555, game_date="2024-07-06", market="moneyline", selection="home",
        model_prob=0.62, prob_source="heuristic", model_version="v1.2.0",
    ))
    session.add(production_db.Pick(
        game_pk=556, game_date="2024-07-06", market="moneyline", selection="home",
        model_prob=0.55, prob_source="skellam", model_version="v1.2.0",
    ))
    # Un pick de run_line en el medio -- nunca debe sincronizarse (no es moneyline).
    session.add(production_db.Pick(
        game_pk=556, game_date="2024-07-06", market="run_line", selection="home",
        model_prob=0.50, prob_source="skellam", model_version="v1.2.0",
    ))
    session.commit()
    session.close()
    return url


def test_sync_legacy_only_syncs_moneyline_picks(tmp_path):
    legacy_url = _seed_legacy_db(tmp_path / "legacy_prod.db")
    unified_url = f"sqlite:///{tmp_path}/unified.db"

    n = sync_legacy.sync_legacy_moneyline_picks(legacy_url, unified_url)
    assert n == 2  # los 2 moneyline, nunca el run_line

    unified_engine = unified_db.get_engine(unified_url)
    rows_555 = unified_db.predictions_for_game(unified_engine, 555)
    assert len(rows_555) == 1
    assert rows_555[0]["system"] == "mlb_legacy"
    assert rows_555[0]["model_name"] == "legacy_heuristic"
    assert rows_555[0]["predicted_winner"] == "home"
    assert rows_555[0]["actual_winner"] == "home"
    assert rows_555[0]["correct"] is True
    assert rows_555[0]["home_win_prob"] == 0.62


def test_sync_legacy_computes_away_side_home_win_prob_correctly(tmp_path):
    """selection='away' con model_prob=X implica home_win_prob=1-X --
    la conversion de convencion 'probabilidad del lado elegido' a
    'probabilidad de que gane home', comparable con JSA/Game Flow."""
    url = f"sqlite:///{tmp_path}/legacy_away.db"
    engine = create_engine(url, future=True)
    production_db.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add(production_db.ActualResult(
        game_pk=777, game_date="2024-05-01", home_score=2, away_score=9, winner="away", total_runs=11,
    ))
    session.add(production_db.Pick(
        game_pk=777, game_date="2024-05-01", market="moneyline", selection="away",
        model_prob=0.70, prob_source="negbin", model_version="v1.0.0",
    ))
    session.commit()
    session.close()

    unified_url = f"sqlite:///{tmp_path}/unified_away.db"
    sync_legacy.sync_legacy_moneyline_picks(url, unified_url)

    unified_engine = unified_db.get_engine(unified_url)
    rows = unified_db.predictions_for_game(unified_engine, 777)
    assert rows[0]["home_win_prob"] == pytest.approx(0.3)  # 1 - 0.70
    assert rows[0]["predicted_winner"] == "away"
    assert rows[0]["correct"] is True


def test_sync_legacy_is_idempotent(tmp_path):
    legacy_url = _seed_legacy_db(tmp_path / "legacy_prod2.db")
    unified_url = f"sqlite:///{tmp_path}/unified2.db"

    sync_legacy.sync_legacy_moneyline_picks(legacy_url, unified_url)
    sync_legacy.sync_legacy_moneyline_picks(legacy_url, unified_url)

    unified_engine = unified_db.get_engine(unified_url)
    result = unified_db.accuracy_by_system_and_model(unified_engine)
    total_games = sum(r["n_games"] for r in result)
    assert total_games == 2  # nunca duplica, incluso corriendo dos veces


def test_sync_legacy_never_writes_to_legacy_production_tables(tmp_path):
    """Verificacion explicita del principio de aislamiento: sincronizar
    NUNCA modifica ninguna fila de la base de origen del modelo legado."""
    import hashlib

    legacy_url = _seed_legacy_db(tmp_path / "legacy_prod3.db")
    engine = create_engine(legacy_url, future=True)

    def _hash_table(table_name: str) -> str:
        from sqlalchemy import text
        with engine.connect() as conn:
            rows = conn.execute(text(f"SELECT * FROM {table_name} ORDER BY rowid")).fetchall()
        return hashlib.sha256("|".join(str(tuple(r)) for r in rows).encode()).hexdigest()

    hashes_before = {t: _hash_table(t) for t in ("picks", "actual_results")}
    sync_legacy.sync_legacy_moneyline_picks(legacy_url, f"sqlite:///{tmp_path}/unified3.db")
    hashes_after = {t: _hash_table(t) for t in ("picks", "actual_results")}

    assert hashes_before == hashes_after
