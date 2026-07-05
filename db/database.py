"""
Capa de base de datos.

Por defecto usa SQLite local (cero configuración, funciona de inmediato).
Para usar PostgreSQL, define la variable de entorno DATABASE_URL, ej:
  export DATABASE_URL="postgresql://usuario:password@localhost:5432/mlb_edge"

Guardamos cada análisis diario para poder ver después si el modelo
realmente hubiera tenido edge real (tracking de resultados).
"""

from datetime import datetime

from sqlalchemy import (
    create_engine, event, Column, Integer, String, Float, DateTime, UniqueConstraint
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
    away_edge = Column(Float, nullable=True)
    home_edge = Column(Float, nullable=True)
    away_ev = Column(Float, nullable=True)
    home_ev = Column(Float, nullable=True)
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


def init_db():
    """Crea las tablas si no existen. Llamar una vez al arrancar el proyecto."""
    Base.metadata.create_all(engine)


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