"""
Pruebas de db/database.py: idempotencia (upsert), Feature Snapshot Store, y
cálculo de CLV. Cada test corre contra un SQLite temporal y descartable —
se redirige `engine`/`SessionLocal` del módulo real, nunca se toca
mlb_edge.db ni se deja estado que contamine otros tests.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.database as database


def test_utcnow_naive_returns_naive_datetime_close_to_now():
    """Reemplazo de datetime.utcnow() (deprecado en Python 3.12+) -- debe
    seguir devolviendo un datetime NAIVE (sin tzinfo), no uno aware, para
    no cambiar el formato ya almacenado en columnas DateTime existentes.
    Se compara contra datetime.now(timezone.utc), no contra utcnow(), para
    que este propio test no dispare la advertencia de deprecación."""
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    result = database._utcnow_naive()
    after = datetime.now(timezone.utc).replace(tzinfo=None)

    assert result.tzinfo is None
    assert before <= result <= after


def test_sqlite_pragmas_set_wal_synchronous_and_foreign_keys(tmp_path):
    """
    El listener de conexión se registra vía @event.listens_for(engine, ...)
    contra la instancia real de `engine` creada al importar el módulo -- no
    se re-dispara si luego se monkeypatchea `database.engine` a un motor
    nuevo (como hace el fixture isolated_db de abajo). Por eso se prueba
    la función del listener directamente contra una conexión sqlite3 real
    (con archivo, no :memory: -- SQLite reporta journal_mode='memory' para
    bases en memoria sin importar qué PRAGMA se pida, así que probarlo ahí
    daría un falso negativo para WAL específicamente).
    """
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "pragma_test.db"))
    try:
        database._enable_sqlite_pragmas(conn, None)
        cur = conn.cursor()
        assert cur.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert cur.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert cur.execute("PRAGMA foreign_keys").fetchone()[0] == 1  # ON
    finally:
        conn.close()


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    temp_engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setattr(database, "engine", temp_engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=temp_engine))
    database.init_db()
    return database


def test_save_analysis_upserts_instead_of_duplicating(isolated_db):
    row = {
        "game_pk": 1, "game_date": "2026-07-05", "away_team": "A", "home_team": "B",
        "away_model_prob": 0.6, "home_model_prob": 0.4,
    }
    isolated_db.save_analysis(row)
    isolated_db.save_analysis(row)  # re-ejecución del pipeline el mismo día

    session = isolated_db.SessionLocal()
    try:
        count = session.query(isolated_db.GameAnalysis).count()
    finally:
        session.close()
    assert count == 1


def test_feature_snapshot_round_trip(isolated_db):
    raw_inputs = {"away_era": 3.5, "home_era": 4.1, "park_factor": 1.02}
    isolated_db.save_feature_snapshot(game_pk=42, game_date="2026-07-05", raw_inputs=raw_inputs)

    snap = isolated_db.get_feature_snapshot(game_pk=42, game_date="2026-07-05")

    assert snap is not None
    assert snap["raw_inputs"] == raw_inputs


def test_feature_snapshot_upserts_instead_of_duplicating(isolated_db):
    isolated_db.save_feature_snapshot(game_pk=42, game_date="2026-07-05", raw_inputs={"v": 1})
    isolated_db.save_feature_snapshot(game_pk=42, game_date="2026-07-05", raw_inputs={"v": 2})

    session = isolated_db.SessionLocal()
    try:
        count = session.query(isolated_db.FeatureSnapshot).count()
    finally:
        session.close()
    assert count == 1

    snap = isolated_db.get_feature_snapshot(game_pk=42, game_date="2026-07-05")
    assert snap["raw_inputs"] == {"v": 2}


def test_get_feature_snapshot_returns_none_when_missing(isolated_db):
    assert isolated_db.get_feature_snapshot(game_pk=999, game_date="2026-07-05") is None


def test_feature_snapshot_stores_frozen_config_columns(isolated_db):
    raw_inputs = {
        "away_era": 3.5, "home_era": 4.1, "park_factor": 1.02,
        "park_factor_weight": 1.0, "weather_correction": 0.0,
        "starter_weight": 0.65, "home_field_advantage": 0.02,
    }
    isolated_db.save_feature_snapshot(game_pk=42, game_date="2026-07-05", raw_inputs=raw_inputs)

    session = isolated_db.SessionLocal()
    try:
        snap = (
            session.query(isolated_db.FeatureSnapshot)
            .filter_by(game_pk=42, game_date="2026-07-05")
            .one()
        )
    finally:
        session.close()

    assert snap.park_factor_weight == 1.0
    assert snap.weather_correction == 0.0
    assert snap.starter_weight == 0.65
    assert snap.home_field_advantage == 0.02


def test_game_analysis_has_game_date_leftmost_index(isolated_db):
    from sqlalchemy import inspect
    inspector = inspect(isolated_db.engine)
    indexes = [ix["name"] for ix in inspector.get_indexes("game_analysis")]
    assert any("game_date" in ix for ix in indexes)


def test_record_closing_odds_computes_positive_clv_when_line_moves_toward_your_side(isolated_db):
    bet_id = isolated_db.record_bet({
        "game_pk": 7, "game_date": "2026-07-05", "market": "moneyline",
        "side": "away", "odds": -135, "model_prob": 0.6, "stake": 1.0,
    })

    updated = isolated_db.record_closing_odds(game_pk=7, side="away", closing_odds=-160)
    assert updated == 1

    session = isolated_db.SessionLocal()
    try:
        bet = session.get(isolated_db.Bet, bet_id)
        # el mercado se movió a favor de tu lado (-135 -> -160): CLV positivo
        assert bet.clv > 0
        assert bet.closing_odds == -160
    finally:
        session.close()


def test_record_closing_odds_negative_when_line_moves_against_your_side(isolated_db):
    bet_id = isolated_db.record_bet({
        "game_pk": 8, "game_date": "2026-07-05", "market": "moneyline",
        "side": "home", "odds": 120, "model_prob": 0.5, "stake": 1.0,
    })

    isolated_db.record_closing_odds(game_pk=8, side="home", closing_odds=150)

    session = isolated_db.SessionLocal()
    try:
        bet = session.get(isolated_db.Bet, bet_id)
        assert bet.clv < 0
    finally:
        session.close()


def test_get_pending_moneyline_bets_excludes_bets_with_closing_odds_already_set(isolated_db):
    isolated_db.record_bet({
        "game_pk": 1, "game_date": "2026-07-05", "market": "moneyline",
        "side": "away", "odds": -135, "model_prob": 0.6, "stake": 1.0,
    })
    settled_id = isolated_db.record_bet({
        "game_pk": 2, "game_date": "2026-07-05", "market": "moneyline",
        "side": "home", "odds": 120, "model_prob": 0.5, "stake": 1.0,
    })
    isolated_db.record_closing_odds(game_pk=2, side="home", closing_odds=110)

    pending = isolated_db.get_pending_moneyline_bets("2026-07-05")

    assert len(pending) == 1
    assert pending[0]["game_pk"] == 1
    assert pending[0]["side"] == "away"


def test_get_pending_moneyline_bets_filters_by_date(isolated_db):
    isolated_db.record_bet({
        "game_pk": 3, "game_date": "2026-07-04", "market": "moneyline",
        "side": "away", "odds": -135, "model_prob": 0.6, "stake": 1.0,
    })

    assert isolated_db.get_pending_moneyline_bets("2026-07-05") == []


def _make_pick(market, selection, line=None, odds_used=-110, forced=False, model_prob=0.6, edge=0.05, ev=0.06):
    return {
        "market": market, "selection": selection, "line": line,
        "model_prob": model_prob, "market_prob": 0.5, "edge": edge, "ev": ev,
        "odds_used": odds_used, "forced": forced,
    }


def test_save_picks_upserts_instead_of_duplicating(isolated_db):
    picks = [_make_pick("moneyline", "away")]
    isolated_db.save_picks(1, "2026-07-05", picks, model_version="v1")
    isolated_db.save_picks(1, "2026-07-05", picks, model_version="v2")  # re-ejecución del mismo día

    session = isolated_db.SessionLocal()
    try:
        rows = session.query(isolated_db.Pick).filter_by(game_pk=1, game_date="2026-07-05").all()
        assert len(rows) == 1
        assert rows[0].model_version == "v2"
    finally:
        session.close()


def test_save_picks_persists_prob_source_and_directional_discrepancy(isolated_db):
    pick = _make_pick("moneyline", "away")
    pick["prob_source"] = "skellam"
    pick["directional_discrepancy"] = True

    isolated_db.save_picks(1, "2026-07-05", [pick], model_version="v1")

    session = isolated_db.SessionLocal()
    try:
        row = session.query(isolated_db.Pick).filter_by(game_pk=1, game_date="2026-07-05").one()
        assert row.prob_source == "skellam"
        assert row.directional_discrepancy is True
    finally:
        session.close()


def test_save_picks_defaults_prob_source_to_none_when_absent(isolated_db):
    # _make_pick no manda prob_source/directional_discrepancy -- no debe
    # romper el guardado (compatibilidad con picks generados antes de que
    # existieran estos campos).
    isolated_db.save_picks(1, "2026-07-05", [_make_pick("moneyline", "away")], model_version="v1")

    session = isolated_db.SessionLocal()
    try:
        row = session.query(isolated_db.Pick).filter_by(game_pk=1, game_date="2026-07-05").one()
        assert row.prob_source is None
        assert row.directional_discrepancy is None
    finally:
        session.close()


def test_save_picks_allows_multiple_markets_same_game(isolated_db):
    picks = [
        _make_pick("moneyline", "away"),
        _make_pick("run_line", "away", line=1.5),
        _make_pick("totals", "over", line=8.5),
    ]
    isolated_db.save_picks(1, "2026-07-05", picks, model_version="v1")

    session = isolated_db.SessionLocal()
    try:
        rows = session.query(isolated_db.Pick).filter_by(game_pk=1, game_date="2026-07-05").all()
        assert len(rows) == 3
        assert {r.market for r in rows} == {"moneyline", "run_line", "totals"}
    finally:
        session.close()


def test_settle_picks_for_game_moneyline(isolated_db):
    isolated_db.save_picks(1, "2026-07-05", [_make_pick("moneyline", "away", odds_used=-150)], "v1")

    settled = isolated_db.settle_picks_for_game(1, {
        "home_score": 2, "away_score": 5, "winner": "away", "total_runs": 7,
    })

    assert settled == 1
    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="moneyline").one()
        assert pick.result == "win"
        assert abs(pick.profit_unit - (100 / 150)) < 1e-9
    finally:
        session.close()


def test_settle_picks_for_game_run_line_home_covers(isolated_db):
    isolated_db.save_picks(1, "2026-07-05", [_make_pick("run_line", "home", line=1.5, odds_used=120)], "v1")

    # Local gana por 3 -> cubre -1.5
    isolated_db.settle_picks_for_game(1, {
        "home_score": 6, "away_score": 3, "winner": "home", "total_runs": 9,
    })

    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="run_line").one()
        assert pick.result == "win"
    finally:
        session.close()


def test_settle_picks_for_game_run_line_home_fails_to_cover(isolated_db):
    isolated_db.save_picks(1, "2026-07-05", [_make_pick("run_line", "home", line=1.5, odds_used=120)], "v1")

    # Local gana por solo 1 -> NO cubre -1.5
    isolated_db.settle_picks_for_game(1, {
        "home_score": 4, "away_score": 3, "winner": "home", "total_runs": 7,
    })

    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="run_line").one()
        assert pick.result == "loss"
    finally:
        session.close()


def test_settle_picks_for_game_run_line_away_covers_by_losing_close(isolated_db):
    isolated_db.save_picks(1, "2026-07-05", [_make_pick("run_line", "away", line=1.5, odds_used=-140)], "v1")

    # Visitante pierde por 1 -> cubre +1.5
    isolated_db.settle_picks_for_game(1, {
        "home_score": 4, "away_score": 3, "winner": "home", "total_runs": 7,
    })

    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="run_line").one()
        assert pick.result == "win"
    finally:
        session.close()


def test_settle_picks_for_game_totals_over_under(isolated_db):
    isolated_db.save_picks(1, "2026-07-05", [
        _make_pick("totals", "over", line=8.5, odds_used=-110),
    ], "v1")

    isolated_db.settle_picks_for_game(1, {
        "home_score": 5, "away_score": 5, "winner": "home", "total_runs": 10,
    })

    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="totals").one()
        assert pick.result == "win"
    finally:
        session.close()


def test_settle_picks_for_game_totals_push_on_exact_line(isolated_db):
    isolated_db.save_picks(1, "2026-07-05", [
        _make_pick("totals", "over", line=9, odds_used=-110),  # línea entera -- push posible
    ], "v1")

    isolated_db.settle_picks_for_game(1, {
        "home_score": 5, "away_score": 4, "winner": "home", "total_runs": 9,
    })

    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="totals").one()
        assert pick.result == "push"
        assert pick.profit_unit == 0.0
    finally:
        session.close()


def test_settle_picks_for_game_only_settles_pending_picks(isolated_db):
    isolated_db.save_picks(1, "2026-07-05", [_make_pick("moneyline", "away")], "v1")
    isolated_db.settle_picks_for_game(1, {"home_score": 2, "away_score": 5, "winner": "away", "total_runs": 7})

    # Segunda liquidación del mismo juego no debe volver a tocar el pick ya resuelto
    settled_again = isolated_db.settle_picks_for_game(1, {"home_score": 2, "away_score": 5, "winner": "away", "total_runs": 7})
    assert settled_again == 0
