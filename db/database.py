"""
Capa de base de datos.

Por defecto usa SQLite local (cero configuración, funciona de inmediato).
Para usar PostgreSQL, define la variable de entorno DATABASE_URL, ej:
  export DATABASE_URL="postgresql://usuario:password@localhost:5432/mlb_edge"

Guardamos cada análisis diario para poder ver después si el modelo
realmente hubiera tenido edge real (tracking de resultados).
"""

import json
from datetime import datetime, timezone

from sqlalchemy import (
    create_engine, event, inspect, text,
    Column, Integer, String, Float, Boolean, DateTime, Text, UniqueConstraint, Index
)
from sqlalchemy.orm import declarative_base, sessionmaker

from config import DATABASE_URL, MODEL_VERSION

Base = declarative_base()
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def _utcnow_naive() -> datetime:
    """Reemplazo de datetime.utcnow() (deprecado desde Python 3.12,
    eliminado en una versión futura). Devuelve el mismo tipo que el
    original -- un datetime NAIVE en UTC -- para no cambiar el formato ya
    almacenado en las columnas DateTime existentes (que no usan
    timezone=True); solo cambia cómo se obtiene el valor, no su forma."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_pragmas(dbapi_connection, connection_record):
        """
        WAL permite que el dashboard lea mientras el cron escribe, sin
        bloquear ninguno de los dos lados. synchronous=NORMAL es seguro en
        modo WAL (solo arriesga la transacción más reciente ante un corte
        de energía, nunca corrupción) y evita un fsync completo en cada
        commit. foreign_keys=ON no tiene efecto todavía -- ningún modelo
        declara ForeignKey() hoy -- pero lo dejamos activado para cuando
        se agreguen relaciones reales entre tablas.
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()


class GameAnalysis(Base):
    __tablename__ = "game_analysis"
    __table_args__ = (
        # Incluye model_version a propósito: si se sube MODEL_VERSION y se
        # recalcula el mismo día, queda una fila por versión (para comparar
        # rendimiento entre versiones, el objetivo declarado del proyecto),
        # en vez de que la versión nueva sobreescriba a la vieja. El upsert
        # de save_analysis() sigue siendo idempotente dentro de la MISMA
        # versión — correr el pipeline dos veces con el mismo código no
        # duplica filas.
        UniqueConstraint("game_pk", "game_date", "model_version", name="uq_pred"),
        # uq_pred es un índice único, pero su columna izquierda es game_pk,
        # no game_date -- no sirve para acelerar queries que filtran SOLO
        # por game_date (compute_metrics, get_predictions_without_result),
        # que sin esto hacen full table scan. Ver db/migrate_v07.py para
        # bases de datos existentes creadas antes de este índice.
        Index("ix_game_date_pk", "game_date", "game_pk"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_pk = Column(Integer, nullable=False)
    game_date = Column(String, nullable=False)  # YYYY-MM-DD
    away_team = Column(String, nullable=False)
    home_team = Column(String, nullable=False)
    away_pitcher = Column(String)
    home_pitcher = Column(String)
    away_bullpen_era = Column(Float, nullable=True)
    home_bullpen_era = Column(Float, nullable=True)
    away_k_pct = Column(Float, nullable=True)
    home_k_pct = Column(Float, nullable=True)
    away_days_rest = Column(Integer, nullable=True)
    home_days_rest = Column(Integer, nullable=True)
    park_factor = Column(Float, nullable=True)
    park_name = Column(String, nullable=True)
    temp_f = Column(Float, nullable=True)
    away_model_prob = Column(Float)
    home_model_prob = Column(Float)
    away_proj_runs = Column(Float, nullable=True)
    home_proj_runs = Column(Float, nullable=True)
    away_skellam_prob = Column(Float, nullable=True)
    home_skellam_prob = Column(Float, nullable=True)
    away_negbin_prob = Column(Float, nullable=True)
    home_negbin_prob = Column(Float, nullable=True)
    home_covers_rl_prob = Column(Float, nullable=True)
    away_covers_rl_prob = Column(Float, nullable=True)
    fair_total_runs = Column(Float, nullable=True)
    away_market_prob = Column(Float, nullable=True)
    home_market_prob = Column(Float, nullable=True)
    away_market_no_vig_prob = Column(Float, nullable=True)
    home_market_no_vig_prob = Column(Float, nullable=True)
    market_favorite_team = Column(String, nullable=True)
    market_favorite_side = Column(String, nullable=True)  # "home" / "away" / None si pick'em
    market_favorite_prob = Column(Float, nullable=True)
    model_edge_vs_market_favorite = Column(Float, nullable=True)
    away_edge = Column(Float, nullable=True)
    home_edge = Column(Float, nullable=True)
    away_ev = Column(Float, nullable=True)
    home_ev = Column(Float, nullable=True)
    flag_review = Column(Boolean, nullable=True, default=False)
    decision = Column(String, nullable=True)  # tu decisión final, texto libre
    model_version = Column(String, default=MODEL_VERSION)
    git_commit = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow_naive)


class ActualResult(Base):
    """Resultado real de un juego ya jugado, para comparar contra la predicción."""
    __tablename__ = "actual_results"

    game_pk = Column(Integer, primary_key=True)
    game_date = Column(String, nullable=False)
    home_score = Column(Integer, nullable=False)
    away_score = Column(Integer, nullable=False)
    winner = Column(String, nullable=False)  # "home" o "away"
    total_runs = Column(Integer, nullable=False)
    updated_at = Column(DateTime, default=_utcnow_naive)


class Bet(Base):
    """
    Apuesta REAL que decidiste hacer. Separada de GameAnalysis a propósito:
    no toda predicción se convierte en apuesta, y una apuesta necesita
    stake/cuota/resultado/profit que no le pertenecen a la predicción en sí.
    """
    __tablename__ = "bets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_pk = Column(Integer, nullable=False)
    game_date = Column(String, nullable=False)
    market = Column(String, default="moneyline")  # moneyline por ahora; run_line/total a futuro
    side = Column(String, nullable=False)  # "home" o "away"
    odds = Column(Float, nullable=False)
    model_prob = Column(Float, nullable=False)
    expected_value = Column(Float, nullable=True)  # EV por unidad, calculado al momento de apostar
    stake = Column(Float, nullable=False)
    placed_at = Column(DateTime, default=_utcnow_naive)
    result = Column(String, default="pending")  # pending, win, loss
    profit = Column(Float, nullable=True)  # se llena al liquidar
    closing_odds = Column(Float, nullable=True)  # cuota del mismo lado cerca del inicio del juego
    clv = Column(Float, nullable=True)  # closing line value, en probabilidad (ver record_closing_odds)


class Pick(Base):
    """
    Recomendación generada POR EL SISTEMA — separada de Bet a propósito,
    igual que Bet está separada de GameAnalysis: un Pick es lo que el
    modelo recomienda (unidades nocionales, 1u pareja por pick); Bet es el
    dinero real que decidiste apostar. No todo Pick se convierte en Bet, y
    liquidar Picks no debe tocar el ROI real de Bet.

    Hasta 3 por partido (uno por mercado: moneyline, run_line, totals).
    `forced=True` marca los picks generados sin edge real, solo para
    cumplir la regla de "siempre al menos 1 pick por partido" — las
    métricas de desempeño los mantienen separados de los picks reales.
    """
    __tablename__ = "picks"
    __table_args__ = (
        UniqueConstraint("game_pk", "game_date", "market", "selection",
                         name="uq_pick_game_market_selection"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_pk = Column(Integer, nullable=False)
    game_date = Column(String, nullable=False)
    market = Column(String, nullable=False)      # "moneyline" | "run_line" | "totals"
    selection = Column(String, nullable=False)   # "home"/"away" (ML, RL) o "over"/"under" (totales)
    line = Column(Float, nullable=True)          # null en ML; -1.5/+1.5 en RL; ej. 8.5 en totales
    model_prob = Column(Float, nullable=False)
    market_prob = Column(Float, nullable=True)
    edge = Column(Float, nullable=True)
    ev = Column(Float, nullable=True)
    odds_used = Column(Float, nullable=True)
    forced = Column(Boolean, nullable=False, default=False)
    # Qué modelo alimentó model_prob para este pick ("heuristic"/"skellam"/
    # "negbin", ver config.PICK_PROBABILITY_SOURCE y model/picks.py) --
    # trazabilidad: si se recalibra o se cambia la fuente en el futuro, un
    # pick viejo sigue diciendo con qué modelo se generó de verdad.
    prob_source = Column(String, nullable=True)
    # True cuando el modelo que generó este pick (prob_source) favorece un
    # lado distinto al que favorece el heurístico -- una señal de que el
    # cambio de fuente de probabilidad realmente movió la recomendación,
    # no solo el número. None cuando no aplica (run_line/totals nunca
    # tuvieron una versión heurística que comparar).
    directional_discrepancy = Column(Boolean, nullable=True)
    result = Column(String, default="pending")   # pending / win / loss / push
    profit_unit = Column(Float, nullable=True)   # ganancia por 1 unidad nocional (NO dinero real)
    model_version = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow_naive)


class FeatureSnapshot(Base):
    """
    Insumos crudos (ERA, OPS, bullpen, parque, clima, cuotas) usados para
    generar una predicción, congelados con su fecha de captura.

    Sin esto, "recalcular un juego histórico" significaría volver a
    consultar la MLB Stats API por sus stats de temporada *actuales* —que
    ya incluyen partidos posteriores al que se está recalculando— y
    contaminar el backtest con información del futuro (data leakage). Todo
    recálculo histórico debe leer de aquí, nunca volver a golpear la API en
    vivo.
    """
    __tablename__ = "feature_snapshots"
    __table_args__ = (
        UniqueConstraint("game_pk", "game_date", name="uq_feature_snapshot_game_pk_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_pk = Column(Integer, nullable=False)
    game_date = Column(String, nullable=False)
    captured_at = Column(DateTime, default=_utcnow_naive)
    model_version = Column(String, default=MODEL_VERSION)
    raw_inputs_json = Column(Text, nullable=False)  # dict serializado — ver save_feature_snapshot

    # Congelados aparte del blob de arriba (que ya los incluye) para que se
    # puedan auditar/consultar sin deserializar JSON. Sin esto, si
    # PARK_FACTOR_WEIGHT/WEATHER_CORRECTION cambian en config.py después de
    # esta fecha, recalcular este juego con model.predictor leería los
    # valores NUEVOS en vez de los que realmente se usaron -- rompiendo la
    # reproducibilidad exacta que este Store existe para garantizar.
    park_factor_weight = Column(Float, nullable=True)
    weather_correction = Column(Float, nullable=True)
    starter_weight = Column(Float, nullable=True)
    home_field_advantage = Column(Float, nullable=True)


def _auto_add_missing_columns():
    """
    SQLAlchemy `create_all()` solo crea tablas que no existen — nunca altera
    una tabla ya presente en el archivo `.db` del usuario. Como este
    proyecto todavía no tiene un framework de migraciones (Alembic queda
    para cuando el esquema esté más estable), cada columna nueva que se
    agrega a un modelo existente rompería el primer INSERT contra una base
    de datos creada con una versión anterior del código.

    Este helper es una salvaguarda mínima y segura: solo AGREGA columnas
    nullable que falten, nunca borra ni altera columnas existentes. Correr
    dos veces es inofensivo (ALTER TABLE ADD COLUMN falla si ya existe, y
    ese error se ignora a propósito).
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                col_type = column.type.compile(engine.dialect)
                try:
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}"))
                except Exception:
                    # Ya existe, o el dialecto no soporta ALTER simple — no es fatal.
                    pass


def init_db():
    """Crea las tablas que falten y agrega columnas nuevas a las que ya
    existan. Llamar una vez al arrancar el proyecto."""
    Base.metadata.create_all(engine)
    _auto_add_missing_columns()


def save_feature_snapshot(game_pk: int, game_date: str, raw_inputs: dict) -> None:
    """Congela los insumos crudos de una predicción. Upsert por
    (game_pk, game_date), igual que save_analysis — re-ejecutar el pipeline
    el mismo día actualiza el snapshot, no lo duplica."""
    session = SessionLocal()
    try:
        existing = (
            session.query(FeatureSnapshot)
            .filter_by(game_pk=game_pk, game_date=game_date)
            .one_or_none()
        )
        payload = json.dumps(raw_inputs, default=str)
        frozen_config = {
            "park_factor_weight": raw_inputs.get("park_factor_weight"),
            "weather_correction": raw_inputs.get("weather_correction"),
            "starter_weight": raw_inputs.get("starter_weight"),
            "home_field_advantage": raw_inputs.get("home_field_advantage"),
        }
        if existing:
            existing.raw_inputs_json = payload
            existing.captured_at = _utcnow_naive()
            existing.model_version = MODEL_VERSION
            for key, value in frozen_config.items():
                setattr(existing, key, value)
        else:
            session.add(FeatureSnapshot(
                game_pk=game_pk, game_date=game_date,
                raw_inputs_json=payload, model_version=MODEL_VERSION,
                **frozen_config,
            ))
        session.commit()
    finally:
        session.close()


def get_feature_snapshot(game_pk: int, game_date: str) -> dict | None:
    """Recupera los insumos crudos congelados de una predicción, para
    recalcularla con un modelo nuevo sin volver a golpear ninguna API."""
    session = SessionLocal()
    try:
        snap = (
            session.query(FeatureSnapshot)
            .filter_by(game_pk=game_pk, game_date=game_date)
            .one_or_none()
        )
        if snap is None:
            return None
        return {
            "raw_inputs": json.loads(snap.raw_inputs_json),
            "captured_at": snap.captured_at,
            "model_version": snap.model_version,
        }
    finally:
        session.close()


def save_analysis(row: dict) -> None:
    """Upsert por (game_pk, game_date, model_version): re-ejecutar el
    pipeline el mismo día CON LA MISMA VERSIÓN DE MODELO actualiza la fila
    en vez de duplicarla. Si cambia model_version, se guarda como fila
    nueva a propósito (permite comparar versiones del modelo entre sí) —
    ver el comentario del UniqueConstraint en GameAnalysis.

    Resuelve model_version explícitamente a MODEL_VERSION si el caller no
    lo pasó: la columna tiene default=MODEL_VERSION a nivel de SQLAlchemy,
    pero ese default solo se aplica al insertar — si la búsqueda del upsert
    comparara contra None en vez del valor que realmente va a quedar
    grabado, nunca encontraría la fila existente y duplicaría el insert."""
    model_version = row.get("model_version") or MODEL_VERSION
    row = {**row, "model_version": model_version}

    session = SessionLocal()
    try:
        existing = (
            session.query(GameAnalysis)
            .filter_by(game_pk=row["game_pk"], game_date=row["game_date"],
                       model_version=model_version)
            .one_or_none()
        )
        if existing:
            for key, value in row.items():
                setattr(existing, key, value)
        else:
            session.add(GameAnalysis(**row))
        session.commit()
    finally:
        session.close()


def save_picks(game_pk: int, game_date: str, picks: list[dict], model_version: str) -> None:
    """
    Upsert por (game_pk, game_date, market, selection): re-ejecutar el
    pipeline el mismo día actualiza los picks existentes en vez de
    duplicarlos — mismo principio de idempotencia que save_analysis().
    """
    session = SessionLocal()
    try:
        for p in picks:
            existing = (
                session.query(Pick)
                .filter_by(game_pk=game_pk, game_date=game_date,
                           market=p["market"], selection=p["selection"])
                .one_or_none()
            )
            fields = {
                "line": p.get("line"),
                "model_prob": p["model_prob"],
                "market_prob": p.get("market_prob"),
                "edge": p.get("edge"),
                "ev": p.get("ev"),
                "odds_used": p.get("odds_used"),
                "forced": p.get("forced", False),
                "prob_source": p.get("prob_source"),
                "directional_discrepancy": p.get("directional_discrepancy"),
                "model_version": model_version,
            }
            if existing:
                for key, value in fields.items():
                    setattr(existing, key, value)
            else:
                session.add(Pick(game_pk=game_pk, game_date=game_date,
                                  market=p["market"], selection=p["selection"], **fields))
        session.commit()
    finally:
        session.close()


def _resolve_pick_outcome(pick: "Pick", result: dict) -> str:
    """Determina win/loss/push de un Pick contra el resultado real del
    juego. `result` es el mismo dict que usa save_result / settle_bets_for_game
    (home_score, away_score, winner, total_runs)."""
    if pick.market == "moneyline":
        return "win" if pick.selection == result["winner"] else "loss"

    if pick.market == "run_line":
        diff = result["home_score"] - result["away_score"]
        if pick.selection == "home":
            return "win" if diff >= 2 else "loss"
        return "win" if diff <= 1 else "loss"  # visitante cubre +1.5: pierde por 1 o gana

    if pick.market == "totals":
        if result["total_runs"] == pick.line:
            return "push"
        over_hit = result["total_runs"] > pick.line
        if pick.selection == "over":
            return "win" if over_hit else "loss"
        return "win" if not over_hit else "loss"

    raise ValueError(f"Mercado desconocido en Pick: {pick.market}")


def settle_picks_for_game(game_pk: int, result: dict) -> int:
    """
    Liquida TODOS los picks pendientes de un juego ya terminado (moneyline,
    run_line, totals), calculando profit_unit en unidades nocionales (1u
    pareja por pick) — separado del profit/stake real de Bet.
    """
    session = SessionLocal()
    try:
        picks = (
            session.query(Pick)
            .filter(Pick.game_pk == game_pk, Pick.result == "pending")
            .all()
        )
        for p in picks:
            outcome = _resolve_pick_outcome(p, result)
            p.result = outcome
            if outcome == "push":
                p.profit_unit = 0.0
            elif outcome == "win":
                p.profit_unit = (100 / abs(p.odds_used)) if p.odds_used < 0 else (p.odds_used / 100)
            else:
                p.profit_unit = -1.0
        session.commit()
        return len(picks)
    finally:
        session.close()


def save_result(result_row: dict) -> None:
    """Guarda (o actualiza) el resultado real de un juego."""
    session = SessionLocal()
    try:
        existing = session.get(ActualResult, result_row["game_pk"])
        if existing:
            for key, value in result_row.items():
                setattr(existing, key, value)
        else:
            session.add(ActualResult(**result_row))
        session.commit()
    finally:
        session.close()


def record_bet(bet_row: dict) -> int:
    """Registra una apuesta real que decidiste hacer. Devuelve el id de la apuesta."""
    session = SessionLocal()
    try:
        bet = Bet(**bet_row)
        session.add(bet)
        session.commit()
        return bet.id
    finally:
        session.close()


def settle_bets_for_game(game_pk: int, winner: str) -> int:
    """
    Liquida las apuestas moneyline pendientes de un juego que ya terminó.
    Devuelve cuántas apuestas se liquidaron.
    """
    session = SessionLocal()
    try:
        pending = (
            session.query(Bet)
            .filter(Bet.game_pk == game_pk, Bet.result == "pending", Bet.market == "moneyline")
            .all()
        )
        for bet in pending:
            won = (bet.side == winner)
            bet.result = "win" if won else "loss"
            if won:
                b = (100 / abs(bet.odds)) if bet.odds < 0 else (bet.odds / 100)
                bet.profit = bet.stake * b
            else:
                bet.profit = -bet.stake
        session.commit()
        return len(pending)
    finally:
        session.close()


def record_closing_odds(game_pk: int, side: str, closing_odds: float) -> int:
    """
    Registra la cuota de cierre para las apuestas pendientes/liquidadas de
    un juego y lado dados, y calcula el CLV en espacio de probabilidad:

        clv = implied_prob(closing_odds) - implied_prob(tu cuota al apostar)

    Positivo = el mercado se movió hacia tu lado después de que apostaste
    (bateaste la línea de cierre) — el indicador que la industria usa para
    separar skill real de varianza favorable en muestra chica.

    Nota: "cierre" aquí depende de CUÁNDO se llame esta función. Capturar
    la cuota exacta al inicio del juego requiere un scheduler corriendo a
    esa hora exacta (Orchestration Engine, fuera del alcance de esta fase)
    — hoy es responsabilidad de quien invoque esta función llamarla lo más
    cerca posible del inicio real del partido.
    """
    from model.edge import implied_prob

    session = SessionLocal()
    try:
        bets = (
            session.query(Bet)
            .filter(Bet.game_pk == game_pk, Bet.side == side, Bet.market == "moneyline")
            .all()
        )
        for bet in bets:
            bet.closing_odds = closing_odds
            bet.clv = implied_prob(closing_odds) - implied_prob(bet.odds)
        session.commit()
        return len(bets)
    finally:
        session.close()


def get_pending_moneyline_bets(game_date: str) -> list[dict]:
    """Apuestas moneyline de una fecha dada que todavía no tienen cuota de
    cierre registrada — para que scripts/capture_closing_lines.py sepa
    cuáles buscar sin tener que consultar la tabla completa cada vez."""
    session = SessionLocal()
    try:
        bets = (
            session.query(Bet)
            .filter(Bet.game_date == game_date, Bet.market == "moneyline", Bet.closing_odds.is_(None))
            .all()
        )
        return [{"game_pk": b.game_pk, "side": b.side} for b in bets]
    finally:
        session.close()


def get_predictions_without_result(days_back: int = 5) -> list[dict]:
    """Predicciones de los últimos N días que todavía no tienen resultado
    guardado. Deduplicado por game_pk: como GameAnalysis ahora puede tener
    más de una fila por juego (una por model_version, ver UniqueConstraint
    uq_pred), sin este dedup un mismo juego se procesaría dos veces en
    update_results() -- llamadas duplicadas a la API de resultados y un
    contador de "actualizados" inflado."""
    from datetime import date, timedelta

    cutoff = (date.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    session = SessionLocal()
    try:
        existing_pks = {r.game_pk for r in session.query(ActualResult.game_pk).all()}
        predictions = (
            session.query(GameAnalysis)
            .filter(GameAnalysis.game_date >= cutoff, GameAnalysis.game_date <= yesterday)
            .order_by(GameAnalysis.id.desc())
            .all()
        )
        seen_pks = set()
        result = []
        for p in predictions:
            if p.game_pk in existing_pks or p.game_pk in seen_pks:
                continue
            seen_pks.add(p.game_pk)
            result.append({"game_pk": p.game_pk, "game_date": p.game_date,
                            "away_team": p.away_team, "home_team": p.home_team})
        return result
    finally:
        session.close()


if __name__ == "__main__":
    init_db()
    print(f"Base de datos lista en: {DATABASE_URL}")