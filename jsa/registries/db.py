"""Registries -- Secciones 3.2/3.3/4.3/6.2/7.3/10.5bis del spec JSA v3.0.

Todas las tablas son APPEND-ONLY por diseno: nunca se expone un UPDATE ni
un DELETE. Un cambio de estado (ej. una regla que se gradua de
`experimental` a `active`) se modela como una fila NUEVA con el mismo
`*_id` y un `recorded_at` mas reciente -- los consumidores siempre leen
"la fila mas reciente por id" (`latest_by_id`), nunca mutan una fila
existente. Esto es lo que hace verificable el Principio 2 (nunca
sobrescribir ni eliminar registros)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, create_engine, select
from sqlalchemy.engine import Engine

metadata = MetaData()

feature_registry = Table(
    "feature_registry", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("feature_id", String, nullable=False, index=True),
    Column("name", String, nullable=False),
    Column("description", String, nullable=False),
    Column("source", String, nullable=False),
    Column("transformation", String, nullable=False),
    Column("real_correlation", Float, nullable=True),
    Column("model_importance", Float, nullable=True),
    Column("divergence_flag", Boolean, nullable=False, default=False),
    Column("shrinkage_method", String, nullable=True),
    Column("lineage", JSON, nullable=True),
    Column("owner", String, nullable=False),
    Column("date_added", String, nullable=False),
    Column("status", String, nullable=False, default="experimental"),
    Column("version", String, nullable=False),
    Column("validation_experiment", String, nullable=True),
    Column("dependencies", JSON, nullable=False, default=list),
)

rule_registry = Table(
    "rule_registry", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("rule_id", String, nullable=False, index=True),
    Column("trigger", String, nullable=False),
    Column("condition", String, nullable=False),
    Column("weight_adjustments", JSON, nullable=False),
    Column("scientific_justification", String, nullable=False),
    Column("version", String, nullable=False),
    Column("status", String, nullable=False, default="experimental"),
    Column("experiments_supporting_rule", JSON, nullable=False, default=list),
    Column("trace_link", String, nullable=True),
)

pillar_registry = Table(
    "pillar_registry", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("pillar_id", String, nullable=False, index=True),
    Column("status", String, nullable=False, default="experimental"),
    Column("contract_version", String, nullable=False),
    Column("validation_experiment", String, nullable=True),
    Column("date_added", String, nullable=False),
)

market_registry = Table(
    "market_registry", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("market_id", String, nullable=False, index=True),
    Column("description", String, nullable=False),
    Column("data_requirements", JSON, nullable=False, default=list),
    Column("status", String, nullable=False, default="experimental"),
    Column("date_added", String, nullable=False),
)

schema_migration_registry = Table(
    "schema_migration_registry", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("migration_id", String, nullable=False, index=True),
    Column("from_version", String, nullable=False),
    Column("to_version", String, nullable=False),
    Column("change_type", String, nullable=False),
    Column("migration_function", String, nullable=False),
    Column("affected_fields", JSON, nullable=False, default=list),
    Column("date", String, nullable=False),
    Column("backward_read_compatible", Boolean, nullable=False),
)

gate_registry = Table(
    "gate_registry", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("gate_id", String, nullable=False, index=True),
    Column("market", String, nullable=False),
    Column("p_min", Float, nullable=False),
    Column("cri_min", Float, nullable=False),
    Column("uncertainty_max", Float, nullable=False),
    Column("accuracy_wilson_ci_low", Float, nullable=True),
    Column("accuracy_wilson_ci_high", Float, nullable=True),
    Column("coverage_pct", Float, nullable=True),
    Column("coverage_n", Integer, nullable=True),
    Column("status", String, nullable=False, default="under_validation"),
    Column("validation_seasons", JSON, nullable=False, default=list),
    Column("manifest_hash", String, nullable=True),
)

# Schema-only en esta entrega (Seccion 12.6/12.7) -- sin filas todavia:
# ningun experimento ha corrido, ningun modelo se ha promovido. Existen
# para que el mecanismo sea enchufable sin retrofit (Principio 16).
experiment_registry = Table(
    "experiment_registry", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("experiment_id", String, nullable=False, index=True),
    Column("description", String, nullable=False),
    Column("base_model_version", String, nullable=False),
    Column("feature_set", JSON, nullable=False, default=list),
    Column("weights", JSON, nullable=False, default=dict),
    Column("rules", JSON, nullable=False, default=list),
    Column("date_range", JSON, nullable=False),
    Column("metrics_requested", JSON, nullable=False, default=list),
    Column("baseline_comparison", JSON, nullable=False, default=list),
    Column("benchmarking_result", JSON, nullable=True),
    Column("decision", String, nullable=True),
)

model_registry = Table(
    "model_registry", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("model_version", String, nullable=False, index=True),
    Column("git_commit", String, nullable=False),
    Column("linked_experiments", JSON, nullable=False, default=list),
    Column("model_card", JSON, nullable=False, default=dict),
)

# Seccion 8.4.1/9.2 -- una curva de calibracion isotonica ajustada,
# validada via leave-one-season-out (nunca solo un fit unico sobre todos
# los datos). `x_knots`/`y_knots` son los breakpoints de la curva final de
# PRODUCCION (ajustada sobre TODAS las temporadas listadas en
# `seasons_used`, para desplegar); `loso_*` son las metricas agregadas de
# validacion out-of-sample (una temporada afuera por vez, nunca la curva
# de produccion evaluada contra sus propios datos de entrenamiento).
# `status="validated"` es la UNICA condicion que
# `engine/orchestrator.py` puede usar para pasar `calibration_status` de
# "uncalibrated" a "calibrated" -- lo pone el proceso de fit
# (`historical/calibration.py`), nunca a mano.
calibration_registry = Table(
    "calibration_registry", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("calibration_id", String, nullable=False, index=True),
    Column("market", String, nullable=False),
    Column("source_field", String, nullable=False),
    Column("method", String, nullable=False),
    Column("x_knots", JSON, nullable=False),
    Column("y_knots", JSON, nullable=False),
    Column("x_min", Float, nullable=False),
    Column("x_max", Float, nullable=False),
    Column("n_games_fitted", Integer, nullable=False),
    Column("seasons_used", JSON, nullable=False, default=list),
    Column("loso_seasons_validated", JSON, nullable=False, default=list),
    Column("loso_n_games", Integer, nullable=False),
    Column("loso_brier", Float, nullable=False),
    Column("loso_log_loss", Float, nullable=False),
    Column("loso_accuracy", Float, nullable=False),
    Column("loso_ece", Float, nullable=False),
    Column("loso_mce", Float, nullable=False),
    Column("status", String, nullable=False, default="under_validation"),
    Column("date", String, nullable=False),
)


def get_engine(database_url: str) -> Engine:
    return create_engine(database_url, future=True)


def init_registries(engine: Engine) -> None:
    metadata.create_all(engine)


def append(engine: Engine, table: Table, **fields) -> None:
    fields.setdefault("recorded_at", datetime.now(timezone.utc))
    with engine.begin() as conn:
        conn.execute(table.insert().values(**fields))


def latest_by_id(engine: Engine, table: Table, id_column: str) -> dict[str, dict]:
    """Fila mas reciente por cada valor de `id_column` (ej. `feature_id`)
    -- nunca la unica forma de "ver el estado actual" de un registry
    append-only es tomar el `recorded_at` mas alto por id."""
    id_col = table.c[id_column]
    with engine.connect() as conn:
        rows = conn.execute(select(table).order_by(id_col, table.c.recorded_at)).mappings().all()
    latest: dict[str, dict] = {}
    for row in rows:
        latest[row[id_column]] = dict(row)
    return latest


def all_rows(engine: Engine, table: Table) -> list[dict]:
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(table)).mappings().all()]
