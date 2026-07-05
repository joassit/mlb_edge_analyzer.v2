"""
Capa de base de datos.

Por defecto usa SQLite local (cero configuración, funciona de inmediato).
Para usar PostgreSQL, define la variable de entorno DATABASE_URL, ej:
  export DATABASE_URL="postgresql://usuario:password@localhost:5432/mlb_edge"

Guardamos cada análisis diario para poder ver después si el modelo
realmente hubiera tenido edge real (tracking de resultados).
"""

import json
from datetime import datetime

from sqlalchemy import (
    create_engine, event, inspect, text,
    Column, Integer, String, Float, Boolean, DateTime, Text, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, sessionmaker

from config import DATABASE_URL, MODEL_VERSION

Base = declarative_base()
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_wal(dbapi_connection, connection_record):
        """WAL permite que el dashboard lea mientras el cron escribe, sin
        bloquear ninguno de los dos lados."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.close()


class GameAnalysis(Base):
    __tablename__ = "game_analysis"
    __table_args__ = (
        UniqueConstraint("game_pk", "game_date", name="uq_game_analysis_game_pk_date"),
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
    created_at = Column(DateTime, default=datetime.utcnow)


class ActualResult(Base):
    """Resultado real de un juego ya jugado, para comparar contra la predicción."""
    __tablename__ = "actual_results"

    game_pk = Column(Integer, primary_key=True)
    game_date = Column(String, nullable=False)
    home_score = Column(Integer, nullable=False)
    away_score = Column(Integer, nullable=False)
    winner = Column(String, nullable=False)  # "home" o "away"
    total_runs = Column(Integer, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow)


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
    placed_at = Column(DateTime, default=datetime.utcnow)
    result = Column(String, default="pending")  # pending, win, loss
    profit = Column(Float, nullable=True)  # se llena al liquidar
    closing_odds = Column(Float, nullable=True)  # cuota del mismo lado cerca del inicio del juego
    clv = Column(Float, nullable=True)  # closing line value, en probabilidad (ver record_closing_odds)


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
    captured_at = Column(DateTime, default=datetime.utcnow)
    model_version = Column(String, default=MODEL_VERSION)
    raw_inputs_json = Column(Text, nullable=False)  # dict serializado — ver save_feature_snapshot


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
        if existing:
            existing.raw_inputs_json = payload
            existing.captured_at = datetime.utcnow()
            existing.model_version = MODEL_VERSION
        else:
            session.add(FeatureSnapshot(
                game_pk=game_pk, game_date=game_date,
                raw_inputs_json=payload, model_version=MODEL_VERSION,
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
    """Upsert por (game_pk, game_date): si el análisis de ese juego ya existe
    (re-ejecución del pipeline el mismo día), lo actualiza en vez de duplicarlo.
    Sin esto, correr main.py dos veces el mismo día contamina el historial que
    alimenta el Brier Score."""
    session = SessionLocal()
    try:
        existing = (
            session.query(GameAnalysis)
            .filter_by(game_pk=row["game_pk"], game_date=row["game_date"])
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
    """Predicciones de los últimos N días que todavía no tienen resultado guardado."""
    from datetime import date, timedelta

    cutoff = (date.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    session = SessionLocal()
    try:
        existing_pks = {r.game_pk for r in session.query(ActualResult.game_pk).all()}
        predictions = (
            session.query(GameAnalysis)
            .filter(GameAnalysis.game_date >= cutoff, GameAnalysis.game_date <= yesterday)
            .all()
        )
        return [
            {"game_pk": p.game_pk, "game_date": p.game_date,
             "away_team": p.away_team, "home_team": p.home_team}
            for p in predictions if p.game_pk not in existing_pks
        ]
    finally:
        session.close()


if __name__ == "__main__":
    init_db()
    print(f"Base de datos lista en: {DATABASE_URL}")