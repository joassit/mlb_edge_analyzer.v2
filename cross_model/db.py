"""Tabla compartida `unified_model_predictions` -- una fila por
(juego, sistema, modelo, version), consultable con SQL directo entre
sistemas de prediccion independientes.

Honestidad de diseno: `home_win_prob` queda NULL salvo que el sistema de
origen produzca una probabilidad genuinamente calibrada -- hoy ninguno lo
hace (ver `JSAReport.calibration.calibration_status`, siempre
"uncalibrated"). `raw_score` guarda el valor crudo de la senal
(`evidence_score_raw` o el diff de una hipotesis candidata), y
`predicted_winner` se deriva de su signo -- misma convencion que
`jsa/engine/decision_engine.py::one_sentence_explanation()`
("evidencia inclina hacia..."). Nunca se inventa una probabilidad que el
sistema de origen no calculo."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, Integer, MetaData, String, Table, UniqueConstraint, create_engine, select,
)
from sqlalchemy.engine import Engine

from jsa.storage.dialect_utils import upsert

metadata = MetaData()

unified_model_predictions = Table(
    "unified_model_predictions", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("game_pk", Integer, nullable=False),
    Column("game_date", Date, nullable=False),
    Column("season", Integer, nullable=False),
    Column("system", String, nullable=False),
    Column("model_name", String, nullable=False),
    Column("model_version", String, nullable=False),
    Column("raw_score", Float, nullable=True),
    Column("home_win_prob", Float, nullable=True),
    Column("predicted_winner", String, nullable=True),
    Column("actual_winner", String, nullable=True),
    Column("correct", Boolean, nullable=True),
    Column("source_ref", String, nullable=True),
    UniqueConstraint("game_pk", "system", "model_name", "model_version", name="uq_unified_prediction_identity"),
)


def get_engine(database_url: str) -> Engine:
    return create_engine(database_url, future=True)


def init_storage(engine: Engine) -> None:
    metadata.create_all(engine)


def _prediction_row(
    *, game_pk: int, game_date: date, season: int, system: str, model_name: str,
    model_version: str, raw_score: float | None, home_win_prob: float | None,
    predicted_winner: str | None, actual_winner: str | None, source_ref: str | None,
) -> dict:
    correct = (predicted_winner == actual_winner) if (predicted_winner is not None and actual_winner is not None) else None
    return dict(
        recorded_at=datetime.now(timezone.utc), game_pk=game_pk, game_date=game_date, season=season,
        system=system, model_name=model_name, model_version=model_version,
        raw_score=raw_score, home_win_prob=home_win_prob, predicted_winner=predicted_winner,
        actual_winner=actual_winner, correct=correct, source_ref=source_ref,
    )


def upsert_prediction(
    engine: Engine, *, game_pk: int, game_date: date, season: int, system: str, model_name: str,
    model_version: str, raw_score: float | None, home_win_prob: float | None,
    predicted_winner: str | None, actual_winner: str | None, source_ref: str | None,
) -> None:
    """Re-ejecutable: upsert por `(game_pk, system, model_name,
    model_version)` -- nunca duplica filas, y actualiza `actual_winner`/
    `correct` cuando ya se conocen (por ejemplo, si el sync corrio antes
    de que el juego terminara). Conveniencia de UNA fila -- para
    sincronizar muchos juegos usar `upsert_predictions_bulk()`, mucho mas
    rapido contra Postgres remoto (evita un COMMIT/round-trip de red por
    fila)."""
    row = _prediction_row(
        game_pk=game_pk, game_date=game_date, season=season, system=system, model_name=model_name,
        model_version=model_version, raw_score=raw_score, home_win_prob=home_win_prob,
        predicted_winner=predicted_winner, actual_winner=actual_winner, source_ref=source_ref,
    )
    with engine.begin() as conn:
        upsert(conn, unified_model_predictions, index_elements=["game_pk", "system", "model_name", "model_version"], **row)


def upsert_predictions_bulk(engine: Engine, rows: list[dict], *, batch_size: int = 500) -> int:
    """Igual que `upsert_prediction()`, pero para muchas filas a la vez --
    UNA sola transaccion (COMMIT) por lote de `batch_size` en vez de una
    por fila. Contra un Postgres remoto, el costo dominante de escribir
    fila por fila es el commit (fsync de WAL) repetido miles de veces, no
    el INSERT en si -- agrupar en lotes evita esa repeticion sin cambiar
    la semantica de upsert (misma funcion `upsert()` dialect-aware, mismo
    conflicto `(game_pk, system, model_name, model_version)`).

    `rows`: lista de dicts con las MISMAS keys que los kwargs de
    `upsert_prediction()` (`game_pk, game_date, season, system,
    model_name, model_version, raw_score, home_win_prob,
    predicted_winner, actual_winner, source_ref`)."""
    prepared = [_prediction_row(**r) for r in rows]
    for start in range(0, len(prepared), batch_size):
        batch = prepared[start:start + batch_size]
        with engine.begin() as conn:
            for row in batch:
                upsert(conn, unified_model_predictions, index_elements=["game_pk", "system", "model_name", "model_version"], **row)
    return len(prepared)


def predictions_for_game(engine: Engine, game_pk: int) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(unified_model_predictions).where(unified_model_predictions.c.game_pk == game_pk)
        ).mappings().all()
    return [dict(r) for r in rows]


def accuracy_by_system_and_model(engine: Engine, seasons: list[int] | None = None) -> list[dict]:
    """Ejemplo directo de "cruzar resultados con SQL": accuracy real
    (sobre filas con `correct` ya resuelto) agrupada por sistema+modelo,
    sin salir de una sola tabla ni tocar ninguna base de origen."""
    from sqlalchemy import func

    query = (
        select(
            unified_model_predictions.c.system,
            unified_model_predictions.c.model_name,
            unified_model_predictions.c.model_version,
            func.count().label("n_games"),
            func.sum(func.cast(unified_model_predictions.c.correct, Integer)).label("n_correct"),
        )
        .where(unified_model_predictions.c.correct.is_not(None))
        .group_by(
            unified_model_predictions.c.system, unified_model_predictions.c.model_name, unified_model_predictions.c.model_version,
        )
    )
    if seasons:
        query = query.where(unified_model_predictions.c.season.in_(seasons))
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [
        {**dict(r), "accuracy": (r["n_correct"] / r["n_games"]) if r["n_games"] else None}
        for r in rows
    ]
