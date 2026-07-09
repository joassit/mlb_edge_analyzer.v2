"""
Esquema de base de datos del motor histórico -- COMPLETAMENTE separado del
esquema de producción (db/database.py). Ninguna clase de este módulo
hereda de database.Base, ninguna tabla comparte nombre con las de
producción (game_analysis/actual_results/picks/bets/feature_snapshots),
y `engine`/`SessionLocal` de acá NUNCA se conectan al archivo mlb_edge.db
(ver historical_engine/config.py::HISTORICAL_DATABASE_URL).

Ver tests/test_historical_isolation.py::test_historical_base_shares_no_tables_with_production
para la prueba automatizada de que esto se mantiene así.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text,
    UniqueConstraint, Index, create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

from historical_engine.config import HISTORICAL_DATABASE_URL

# Base propia -- deliberadamente NO db.database.Base. Un
# HistoricalBase.metadata.create_all() jamás puede crear ni tocar una
# tabla de producción, porque ni siquiera conoce su existencia.
HistoricalBase = declarative_base()

engine = create_engine(HISTORICAL_DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class HistoricalSeason(HistoricalBase):
    """Una temporada completa (2023/2024/2025/2026, ver config.SUPPORTED_SEASONS).
    Nunca se mezclan resultados entre temporadas -- toda métrica agregada
    en validation.py/model_comparison.py se calcula POR season_year."""
    __tablename__ = "historical_season"

    id = Column(Integer, primary_key=True, autoincrement=True)
    year = Column(Integer, nullable=False, unique=True)
    label = Column(String, nullable=True)
    is_current_season = Column(Boolean, nullable=False, default=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow_naive)


class HistoricalRun(HistoricalBase):
    """Una ejecución del pipeline histórico (temporada completa / mes / rango
    de fechas / equipo / pitcher / juego individual) -- mismo espíritu que
    model_version/git_commit en GameAnalysis de producción: cada fila de
    HistoricalGame/HistoricalAnalysis/HistoricalMetrics queda trazable a
    exactamente qué corrida y qué código la generó."""
    __tablename__ = "historical_run"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_type = Column(String, nullable=False)  # season|month|date_range|team|pitcher|game
    scope_description = Column(String, nullable=False)
    season_year = Column(Integer, nullable=True)
    model_version = Column(String, nullable=True)
    git_commit = Column(String, nullable=True)
    status = Column(String, nullable=False, default="running")  # running|completed|failed
    n_games_processed = Column(Integer, nullable=False, default=0)
    n_games_skipped = Column(Integer, nullable=False, default=0)
    n_errors = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime, default=_utcnow_naive)
    completed_at = Column(DateTime, nullable=True)


class HistoricalGame(HistoricalBase):
    """Metadatos crudos de un juego histórico ingerido -- separado de
    HistoricalAnalysis (que trae las variables RECONSTRUIDAS point-in-time)
    igual que GameAnalysis separa metadatos de features en producción."""
    __tablename__ = "historical_game"
    __table_args__ = (
        UniqueConstraint("game_pk", "run_id", name="uq_historical_game_pk_run"),
        Index("ix_historical_game_season_date", "season_year", "game_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=False)
    game_pk = Column(Integer, nullable=False)
    game_date = Column(String, nullable=False)  # YYYY-MM-DD
    season_year = Column(Integer, nullable=False)
    away_team = Column(String, nullable=False)
    home_team = Column(String, nullable=False)
    away_team_id = Column(Integer, nullable=True)
    home_team_id = Column(Integer, nullable=True)
    away_pitcher_id = Column(Integer, nullable=True)
    home_pitcher_id = Column(Integer, nullable=True)
    away_pitcher_name = Column(String, nullable=True)
    home_pitcher_name = Column(String, nullable=True)
    status = Column(String, nullable=True)
    home_score = Column(Integer, nullable=True)
    away_score = Column(Integer, nullable=True)
    winner = Column(String, nullable=True)  # home|away
    total_runs = Column(Integer, nullable=True)
    ingested_at = Column(DateTime, default=_utcnow_naive)


class HistoricalAnalysis(HistoricalBase):
    """Variables reconstruidas EXCLUSIVAMENTE con información disponible
    antes de `as_of_date` (ver historical_engine/point_in_time_stats.py) --
    el equivalente histórico de GameAnalysis, pero con la fecha de corte
    explícita guardada en cada fila para que cualquier auditoría futura
    pueda verificar que no hubo fuga."""
    __tablename__ = "historical_analysis"
    __table_args__ = (
        Index("ix_historical_analysis_game_run", "game_pk", "run_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=False)
    game_pk = Column(Integer, nullable=False)
    game_date = Column(String, nullable=False)
    season_year = Column(Integer, nullable=False)
    as_of_date = Column(String, nullable=False)  # fecha de corte real usada (siempre < game_date)

    away_era = Column(Float, nullable=True)
    home_era = Column(Float, nullable=True)
    away_ops = Column(Float, nullable=True)
    home_ops = Column(Float, nullable=True)
    away_bullpen_era = Column(Float, nullable=True)
    home_bullpen_era = Column(Float, nullable=True)
    away_k_pct = Column(Float, nullable=True)
    home_k_pct = Column(Float, nullable=True)
    away_bb_pct = Column(Float, nullable=True)
    home_bb_pct = Column(Float, nullable=True)
    away_days_rest = Column(Integer, nullable=True)
    home_days_rest = Column(Integer, nullable=True)
    park_factor = Column(Float, nullable=True)
    park_name = Column(String, nullable=True)
    temp_f = Column(Float, nullable=True)

    away_proj_runs = Column(Float, nullable=True)
    home_proj_runs = Column(Float, nullable=True)
    away_model_prob = Column(Float, nullable=True)   # heurístico
    home_model_prob = Column(Float, nullable=True)
    away_skellam_prob = Column(Float, nullable=True)
    home_skellam_prob = Column(Float, nullable=True)
    away_negbin_prob = Column(Float, nullable=True)
    home_negbin_prob = Column(Float, nullable=True)

    created_at = Column(DateTime, default=_utcnow_naive)


class HistoricalPrediction(HistoricalBase):
    """Una predicción normalizada de UN motor (skellam|negbin|heuristic|
    historical_confidence) para un juego -- forma plana pensada para
    model_comparison.py: una fila por (juego, motor), nunca una columna
    por motor, para poder agregar/filtrar sin acoplarse a cuántos motores
    existan."""
    __tablename__ = "historical_prediction"
    __table_args__ = (
        UniqueConstraint("game_pk", "run_id", "source", name="uq_historical_prediction"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=False)
    game_pk = Column(Integer, nullable=False)
    game_date = Column(String, nullable=False)
    season_year = Column(Integer, nullable=False)
    source = Column(String, nullable=False)  # skellam|negbin|heuristic|historical_confidence
    away_prob = Column(Float, nullable=False)
    home_prob = Column(Float, nullable=False)
    predicted_winner = Column(String, nullable=True)  # home|away
    actual_winner = Column(String, nullable=True)
    correct = Column(Boolean, nullable=True)
    created_at = Column(DateTime, default=_utcnow_naive)


class HistoricalCalibration(HistoricalBase):
    """Reliability diagram por bucket de confianza, por motor, por
    temporada -- mismo concepto que tracking.results_tracker._compute_model_calibration
    de producción, pero nunca la misma tabla ni el mismo dato."""
    __tablename__ = "historical_calibration"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=False)
    season_year = Column(Integer, nullable=False)
    source = Column(String, nullable=False)
    bucket_label = Column(String, nullable=False)
    bucket_low = Column(Float, nullable=False)
    bucket_high = Column(Float, nullable=False)
    n = Column(Integer, nullable=False)
    hits = Column(Integer, nullable=False)
    avg_confidence = Column(Float, nullable=True)
    hit_rate = Column(Float, nullable=True)
    gap = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utcnow_naive)


class HistoricalMetrics(HistoricalBase):
    """Almacén genérico clave-valor de métricas de validación (Brier,
    LogLoss, MAE, RMSE, bias, R², Pearson, Spearman, ECE, MCE, sharpness,
    drift) -- una fila por (corrida, temporada, motor, métrica), para que
    agregar una métrica nueva en validation.py nunca requiera una
    migración de esquema."""
    __tablename__ = "historical_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=False)
    season_year = Column(Integer, nullable=True)  # None = comparación entre temporadas
    source = Column(String, nullable=True)  # None = métrica agregada, no por motor
    metric_name = Column(String, nullable=False)
    metric_value = Column(Float, nullable=True)
    n_sample = Column(Integer, nullable=True)
    extra_json = Column(Text, nullable=True)  # detalle adicional serializado (ej. IC bootstrap)
    created_at = Column(DateTime, default=_utcnow_naive)


class HistoricalSimulation(HistoricalBase):
    """Propuesta de recalibración/optimización de parámetros generada por
    historical_engine/training.py -- NUNCA se aplica automáticamente a
    producción (`applied` siempre queda False; aplicarla es una decisión
    manual fuera de este motor, ver training.py::propose_dispersion_update()).
    """
    __tablename__ = "historical_simulation"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=False)
    season_year = Column(Integer, nullable=True)
    param_name = Column(String, nullable=False)
    baseline_value = Column(Float, nullable=True)
    proposed_value = Column(Float, nullable=True)
    based_on_metric = Column(String, nullable=True)
    baseline_metric_value = Column(Float, nullable=True)
    proposed_metric_value = Column(Float, nullable=True)
    improved = Column(Boolean, nullable=True)
    notes = Column(Text, nullable=True)
    applied = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=_utcnow_naive)


def init_historical_db() -> None:
    """Crea (si no existen) las tablas de este esquema en HISTORICAL_DATABASE_URL.
    Nunca toca ni conoce el esquema de producción -- HistoricalBase.metadata
    solo contiene las clases definidas en este archivo."""
    HistoricalBase.metadata.create_all(engine)
