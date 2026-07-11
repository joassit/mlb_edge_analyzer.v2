"""
Pruebas de db/database.py: idempotencia (upsert), Feature Snapshot Store, y
cálculo de CLV. Cada test corre contra un SQLite temporal y descartable —
se redirige `engine`/`SessionLocal` del módulo real, nunca se toca
mlb_edge.db ni se deja estado que contamine otros tests.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker, Session as OrmSession

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


def test_save_analysis_retries_once_and_succeeds_after_integrity_error(isolated_db, monkeypatch):
    # B1: dos corridas concurrentes pueden insertar la misma fila entre la
    # búsqueda y el commit -- el primer IntegrityError debe reintentarse una
    # vez (segunda búsqueda encuentra la fila ya insertada) en vez de
    # propagar el error.
    original_commit = OrmSession.commit
    call_count = {"n": 0}

    def flaky_commit(self):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise IntegrityError("stmt", "params", Exception("uq_pred"))
        return original_commit(self)

    monkeypatch.setattr(OrmSession, "commit", flaky_commit)

    row = {
        "game_pk": 1, "game_date": "2026-07-05", "away_team": "A", "home_team": "B",
        "away_model_prob": 0.6, "home_model_prob": 0.4,
    }
    isolated_db.save_analysis(row)

    assert call_count["n"] == 2
    session = isolated_db.SessionLocal()
    try:
        count = session.query(isolated_db.GameAnalysis).count()
    finally:
        session.close()
    assert count == 1


def test_save_analysis_raises_if_integrity_error_persists_after_retry(isolated_db, monkeypatch):
    # Si el segundo intento TAMBIÉN choca, el error no debe tragarse en
    # silencio -- debe propagar después de agotar el único reintento.
    def always_fails(self):
        raise IntegrityError("stmt", "params", Exception("uq_pred"))

    monkeypatch.setattr(OrmSession, "commit", always_fails)

    row = {
        "game_pk": 2, "game_date": "2026-07-05", "away_team": "A", "home_team": "B",
        "away_model_prob": 0.6, "home_model_prob": 0.4,
    }
    with pytest.raises(IntegrityError):
        isolated_db.save_analysis(row)


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


def test_auto_add_missing_columns_logs_debug_instead_of_silently_swallowing(tmp_path, monkeypatch, caplog):
    # B4: si un ALTER TABLE ADD COLUMN falla (ej. ya existe, o el dialecto no
    # lo soporta), antes se ignoraba con un `pass` silencioso -- ahora debe
    # quedar rastro en el log a nivel DEBUG en vez de desaparecer sin dejar huella.
    import logging
    import sqlite3
    from sqlalchemy.engine import Connection

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
    monkeypatch.setattr(database, "engine", temp_engine)

    original_execute = Connection.execute

    def flaky_execute(self, statement, *args, **kwargs):
        if "home_model_prob" in str(statement):
            raise Exception("boom - falla simulada de ALTER TABLE")
        return original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(Connection, "execute", flaky_execute)

    with caplog.at_level(logging.DEBUG, logger="mlb_edge_analyzer"):
        database._auto_add_missing_columns()

    assert any("home_model_prob" in r.message for r in caplog.records)


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


def _make_pick(market, selection, line=None, odds_used=-110, forced=False, model_prob=0.6, edge=0.05, ev=0.06,
               favorite_side=None):
    return {
        "market": market, "selection": selection, "line": line,
        "model_prob": model_prob, "market_prob": 0.5, "edge": edge, "ev": ev,
        "odds_used": odds_used, "forced": forced, "favorite_side": favorite_side,
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


def test_save_picks_persists_calibration_phase_true(isolated_db):
    pick = _make_pick("moneyline", "away")
    pick["calibration_phase"] = True

    isolated_db.save_picks(1, "2026-07-05", [pick], model_version="v1")

    session = isolated_db.SessionLocal()
    try:
        row = session.query(isolated_db.Pick).filter_by(game_pk=1, game_date="2026-07-05").one()
        assert row.calibration_phase is True
    finally:
        session.close()


def test_save_picks_defaults_calibration_phase_to_false_when_absent(isolated_db):
    # _make_pick no manda calibration_phase -- compatibilidad con picks
    # generados antes de que existiera este campo (ver Pick.calibration_phase).
    isolated_db.save_picks(1, "2026-07-05", [_make_pick("moneyline", "away")], model_version="v1")

    session = isolated_db.SessionLocal()
    try:
        row = session.query(isolated_db.Pick).filter_by(game_pk=1, game_date="2026-07-05").one()
        assert row.calibration_phase is False
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


def test_save_game_results_persists_all_three_together(isolated_db):
    row = {
        "game_pk": 1, "game_date": "2026-07-05", "away_team": "A", "home_team": "B",
        "away_model_prob": 0.6, "home_model_prob": 0.4,
    }
    snapshot = {"away_era": 3.5}
    picks = [_make_pick("moneyline", "home")]

    isolated_db.save_game_results(row, snapshot, picks, "v1")

    session = isolated_db.SessionLocal()
    try:
        assert session.query(isolated_db.GameAnalysis).filter_by(game_pk=1).count() == 1
        assert session.query(isolated_db.FeatureSnapshot).filter_by(game_pk=1).count() == 1
        assert session.query(isolated_db.Pick).filter_by(game_pk=1).count() == 1
    finally:
        session.close()


def test_save_game_results_rolls_back_all_three_if_picks_fail(isolated_db, monkeypatch):
    """PASO 4: reproduce el problema original de la auditoría -- si guardar
    los picks falla DESPUÉS de que el análisis ya se procesó en la misma
    transacción, antes (3 sesiones/commits separados) el análisis ya
    quedaba comiteado en disco sin sus picks. Ahora comparten sesión: un
    fallo en cualquiera revierte los 3, no queda nada a medias."""
    def _boom(*a, **k):
        raise RuntimeError("fallo simulado guardando picks")

    monkeypatch.setattr(isolated_db, "_upsert_picks", _boom)

    row = {
        "game_pk": 1, "game_date": "2026-07-05", "away_team": "A", "home_team": "B",
        "away_model_prob": 0.6, "home_model_prob": 0.4,
    }
    snapshot = {"away_era": 3.5}
    picks = [_make_pick("moneyline", "home")]

    with pytest.raises(RuntimeError, match="fallo simulado"):
        isolated_db.save_game_results(row, snapshot, picks, "v1")

    session = isolated_db.SessionLocal()
    try:
        # NINGUNO de los 3 debe haber quedado grabado -- ni siquiera el
        # análisis, que en la lógica vieja (3 llamadas separadas) ya
        # habría comiteado exitosamente antes de llegar a los picks.
        assert session.query(isolated_db.GameAnalysis).filter_by(game_pk=1).count() == 0
        assert session.query(isolated_db.FeatureSnapshot).filter_by(game_pk=1).count() == 0
        assert session.query(isolated_db.Pick).filter_by(game_pk=1).count() == 0
    finally:
        session.close()


def test_settle_picks_for_game_works_on_pre_existing_row_with_raw_string_result(isolated_db):
    """PASO 3 (Enums): una fila ya persistida ANTES de este cambio tiene
    result="pending" como string crudo de Python, nunca el Enum -- esto
    inserta esa fila exactamente como quedaría en una DB vieja (bypaseando
    save_picks(), que ahora sí usaría PickResult) y confirma que
    settle_picks_for_game() -- que ahora compara/asigna con PickResult --
    la sigue liquidando bien, sin ninguna migración de datos."""
    session = isolated_db.SessionLocal()
    try:
        session.add(isolated_db.Pick(
            game_pk=1, game_date="2026-07-05", market="moneyline", selection="away",
            model_prob=0.6, market_prob=0.5, edge=0.05, ev=0.06, odds_used=-150,
            result="pending",  # string crudo, no PickResult.PENDING -- simula fila vieja
        ))
        session.commit()
    finally:
        session.close()

    settled = isolated_db.settle_picks_for_game(1, {
        "home_score": 2, "away_score": 5, "winner": "away", "total_runs": 7,
    })
    assert settled == 1

    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="moneyline").one()
        assert pick.result == "win"
        assert pick.result == isolated_db.PickResult.WIN  # compara igual que el Enum, sin migración
    finally:
        session.close()


def test_settle_bets_for_game_works_on_pre_existing_row_with_raw_string_result(isolated_db):
    session = isolated_db.SessionLocal()
    try:
        session.add(isolated_db.Bet(
            game_pk=1, game_date="2026-07-05", market="moneyline", side="away",
            odds=130, model_prob=0.6, stake=1.0,
            result="pending",  # string crudo -- simula fila vieja, no BetResult.PENDING
        ))
        session.commit()
    finally:
        session.close()

    settled = isolated_db.settle_bets_for_game(1, winner="away")
    assert settled == 1

    session = isolated_db.SessionLocal()
    try:
        bet = session.query(isolated_db.Bet).filter_by(game_pk=1).one()
        assert bet.result == "win"
        assert bet.result == isolated_db.BetResult.WIN
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


def test_settle_picks_for_game_run_line_home_covers_alternate_line(isolated_db):
    # Línea alterna -2.5 (no la estándar -1.5) -- antes del fix, la
    # liquidación estaba hardcodeada a diff>=2/diff<=1 sin importar
    # pick.line, así que esto habría liquidado mal (local gana por 3, que
    # SÍ cubre -1.5 pero NO cubre -2.5).
    isolated_db.save_picks(1, "2026-07-05", [_make_pick("run_line", "home", line=2.5, odds_used=120)], "v1")

    # Local gana por 3 -> cubre -2.5 (gana por más de 2.5)
    isolated_db.settle_picks_for_game(1, {
        "home_score": 6, "away_score": 3, "winner": "home", "total_runs": 9,
    })

    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="run_line").one()
        assert pick.result == "win"
    finally:
        session.close()


def test_settle_picks_for_game_run_line_home_fails_alternate_line(isolated_db):
    isolated_db.save_picks(1, "2026-07-05", [_make_pick("run_line", "home", line=2.5, odds_used=120)], "v1")

    # Local gana por 2 -> cubre -1.5 pero NO cubre -2.5
    isolated_db.settle_picks_for_game(1, {
        "home_score": 5, "away_score": 3, "winner": "home", "total_runs": 8,
    })

    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="run_line").one()
        assert pick.result == "loss"
    finally:
        session.close()


def test_settle_picks_for_game_run_line_away_covers_alternate_line(isolated_db):
    # Visitante +2.5: pierde por 2 -> cubre +2.5 (pierde por menos de 2.5)
    isolated_db.save_picks(1, "2026-07-05", [_make_pick("run_line", "away", line=2.5, odds_used=-140)], "v1")

    isolated_db.settle_picks_for_game(1, {
        "home_score": 5, "away_score": 3, "winner": "home", "total_runs": 8,
    })

    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="run_line").one()
        assert pick.result == "win"
    finally:
        session.close()


def test_settle_picks_for_game_run_line_away_fails_alternate_line(isolated_db):
    # Visitante +2.5: pierde por 3 -> NO cubre +2.5
    isolated_db.save_picks(1, "2026-07-05", [_make_pick("run_line", "away", line=2.5, odds_used=-140)], "v1")

    isolated_db.settle_picks_for_game(1, {
        "home_score": 6, "away_score": 3, "winner": "home", "total_runs": 9,
    })

    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="run_line").one()
        assert pick.result == "loss"
    finally:
        session.close()


def test_settle_picks_for_game_run_line_pushes_on_exact_whole_number_line(isolated_db):
    # Línea entera (2.0, no la X.5 estándar) -- diff exacto de 2 empata la
    # línea: debe ser push, no win/loss. Solo alcanzable con una línea
    # entera (nunca ocurre con el -1.5/+1.5 tradicional).
    isolated_db.save_picks(1, "2026-07-05", [_make_pick("run_line", "home", line=2.0, odds_used=120)], "v1")

    isolated_db.settle_picks_for_game(1, {
        "home_score": 5, "away_score": 3, "winner": "home", "total_runs": 8,
    })

    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="run_line").one()
        assert pick.result == "push"
        assert pick.profit_unit == 0.0
    finally:
        session.close()


# --- C1: run_line con el VISITANTE como favorito (favorite_side="away") ---
# Antes, la liquidación asumía SIEMPRE que el local era favorito -- un pick
# sobre el visitante favorito (-1.5) que ganaba por 1 (no cubre) se
# liquidaba como "win" en vez de "loss" (ver la reproducción de este
# defecto contra el código viejo, sin favorite_side, más arriba en esta
# sesión). Los 4 cuadrantes: home-fav cubre/falla, away-fav cubre/falla.

def test_settle_picks_for_game_run_line_away_favorite_covers(isolated_db):
    # Visitante favorito (-1.5), gana por 2 -> SÍ cubre.
    isolated_db.save_picks(1, "2026-07-05", [
        _make_pick("run_line", "away", line=1.5, odds_used=-140, favorite_side="away"),
    ], "v1")

    isolated_db.settle_picks_for_game(1, {
        "home_score": 3, "away_score": 5, "winner": "away", "total_runs": 8,
    })

    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="run_line").one()
        assert pick.favorite_side == "away"
        assert pick.result == "win"
    finally:
        session.close()


def test_settle_picks_for_game_run_line_away_favorite_fails_to_cover(isolated_db):
    # Visitante favorito (-1.5), gana por SOLO 1 -> NO cubre. Este es
    # exactamente el caso que el código viejo (sin favorite_side) liquidaba
    # mal como "win".
    isolated_db.save_picks(1, "2026-07-05", [
        _make_pick("run_line", "away", line=1.5, odds_used=-140, favorite_side="away"),
    ], "v1")

    isolated_db.settle_picks_for_game(1, {
        "home_score": 4, "away_score": 5, "winner": "away", "total_runs": 9,
    })

    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="run_line").one()
        assert pick.result == "loss"
    finally:
        session.close()


def test_settle_picks_for_game_run_line_home_underdog_covers_when_away_is_favorite(isolated_db):
    # Local +1.5 (underdog, visitante es favorito) -- local pierde por 1,
    # menos que la línea -> cubre.
    isolated_db.save_picks(1, "2026-07-05", [
        _make_pick("run_line", "home", line=1.5, odds_used=120, favorite_side="away"),
    ], "v1")

    isolated_db.settle_picks_for_game(1, {
        "home_score": 4, "away_score": 5, "winner": "away", "total_runs": 9,
    })

    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="run_line").one()
        assert pick.result == "win"
    finally:
        session.close()


def test_settle_picks_for_game_run_line_home_underdog_fails_when_away_favorite_covers(isolated_db):
    # Local +1.5 (underdog) -- pierde por 2, el visitante favorito SÍ
    # cubrió su -1.5 -> el local NO cubre su +1.5.
    isolated_db.save_picks(1, "2026-07-05", [
        _make_pick("run_line", "home", line=1.5, odds_used=120, favorite_side="away"),
    ], "v1")

    isolated_db.settle_picks_for_game(1, {
        "home_score": 3, "away_score": 5, "winner": "away", "total_runs": 8,
    })

    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="run_line").one()
        assert pick.result == "loss"
    finally:
        session.close()


def test_save_picks_persists_favorite_side(isolated_db):
    isolated_db.save_picks(1, "2026-07-05", [
        _make_pick("run_line", "away", line=1.5, favorite_side="away"),
    ], "v1")

    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="run_line").one()
        assert pick.favorite_side == "away"
    finally:
        session.close()


def test_save_picks_defaults_favorite_side_to_none_when_absent(isolated_db):
    # _make_pick(favorite_side=None) por default -- compatibilidad con
    # picks viejos (moneyline/totales nunca tuvieron favorite_side).
    isolated_db.save_picks(1, "2026-07-05", [_make_pick("moneyline", "away")], "v1")

    session = isolated_db.SessionLocal()
    try:
        pick = session.query(isolated_db.Pick).filter_by(game_pk=1, market="moneyline").one()
        assert pick.favorite_side is None
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


# --- C4: compatibilidad de sintaxis con dialecto PostgreSQL (sin conectar) ---
# La solución real (Postgres externo vía DATABASE_URL) requiere una cuenta
# que solo el dueño del proyecto puede crear -- esto NO la simula, solo
# verifica que el esquema actual (tipos de columna + DDL completo) es
# sintácticamente válido en dialecto postgres, generado localmente sin
# ninguna conexión de red.

def test_all_column_types_compile_under_postgresql_dialect():
    from sqlalchemy.dialects import postgresql
    dialect = postgresql.dialect()
    for table in database.Base.metadata.sorted_tables:
        for column in table.columns:
            ddl_type = column.type.compile(dialect=dialect)
            assert ddl_type


def test_create_table_ddl_compiles_for_all_tables_under_postgresql():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable
    dialect = postgresql.dialect()
    for table in database.Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert "CREATE TABLE" in ddl


def test_auto_add_missing_columns_alter_statement_is_ansi_compatible():
    # El ALTER TABLE ADD COLUMN que arma _auto_add_missing_columns() es
    # SQL genérico (ANSI), no específico de SQLite -- Postgres soporta la
    # misma sintaxis. Verifica el tipo compilado bajo postgres para una
    # columna real que el proyecto agregó vía migración (favorite_side).
    from sqlalchemy.dialects import postgresql
    dialect = postgresql.dialect()
    col_type = database.Pick.__table__.columns["favorite_side"].type.compile(dialect=dialect)
    statement = f"ALTER TABLE picks ADD COLUMN favorite_side {col_type}"
    assert statement == "ALTER TABLE picks ADD COLUMN favorite_side VARCHAR"


# --- get_predictions_without_result: ventana de 21 días (antes 5) ---
# Un juego pospuesto que tarda más de 5 días en reanudarse bajo el mismo
# game_pk quedaba huérfano (winner=None) para siempre, porque
# update_results() nunca volvía a mirarlo una vez que su game_date caía
# fuera de la ventana -- ver el hallazgo documentado en el informe técnico
# del 2026-07-11 (dos filas huérfanas: 07-07 y 07-10).

def test_get_predictions_without_result_default_window_is_21_days(isolated_db):
    from datetime import date, timedelta

    old_date = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")
    isolated_db.save_analysis({
        "game_pk": 111, "game_date": old_date,
        "away_team": "A", "home_team": "B",
    })

    pending = isolated_db.get_predictions_without_result()

    assert any(p["game_pk"] == 111 for p in pending)


def test_get_predictions_without_result_excludes_games_older_than_window(isolated_db):
    from datetime import date, timedelta

    too_old_date = (date.today() - timedelta(days=25)).strftime("%Y-%m-%d")
    isolated_db.save_analysis({
        "game_pk": 112, "game_date": too_old_date,
        "away_team": "A", "home_team": "B",
    })

    pending = isolated_db.get_predictions_without_result()

    assert all(p["game_pk"] != 112 for p in pending)


def test_get_predictions_without_result_excludes_games_with_saved_result(isolated_db):
    from datetime import date, timedelta

    recent_date = (date.today() - timedelta(days=2)).strftime("%Y-%m-%d")
    isolated_db.save_analysis({
        "game_pk": 113, "game_date": recent_date,
        "away_team": "A", "home_team": "B",
    })
    isolated_db.save_result({
        "game_pk": 113, "game_date": recent_date,
        "home_score": 4, "away_score": 2, "winner": "home", "total_runs": 6,
    })

    pending = isolated_db.get_predictions_without_result()

    assert all(p["game_pk"] != 113 for p in pending)
