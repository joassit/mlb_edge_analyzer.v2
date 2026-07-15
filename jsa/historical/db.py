"""Base de datos propia de `jsa/historical/` -- Feature Store + Results
Store historicos, completamente separados de `jsa/storage/database.py`
(produccion en vivo). Nunca comparten motor de conexion ni archivo por
default (Seccion 4.2 del spec: Feature Store y Results Store nunca se
mezclan; aqui ademas se separa produccion-vs-historico, mismo criterio de
aislamiento que `historical_engine/db.py` en el proyecto hermano)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Column, Date, DateTime, Integer, MetaData, String, Table, UniqueConstraint, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from jsa.storage.dialect_utils import insert_ignore_duplicates

metadata = MetaData()

historical_game = Table(
    "historical_game", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("season", Integer, nullable=False),
    Column("game_pk", Integer, nullable=False),
    Column("game_date", Date, nullable=False),
    Column("home_team", String, nullable=False),
    Column("away_team", String, nullable=False),
    Column("home_team_id", Integer, nullable=False),
    Column("away_team_id", Integer, nullable=False),
    Column("home_pitcher_id", Integer, nullable=True),
    Column("away_pitcher_id", Integer, nullable=True),
    Column("is_double_header", Integer, nullable=False, default=0),
    Column("home_score", Integer, nullable=True),
    Column("away_score", Integer, nullable=True),
    Column("winner", String, nullable=True),
    UniqueConstraint("game_pk", name="uq_historical_game_pk"),
)

historical_snapshot = Table(
    "historical_snapshot", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("season", Integer, nullable=False),
    Column("game_pk", Integer, nullable=False),
    Column("game_date", Date, nullable=False),
    Column("snapshot_hash", String, nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("game_pk", name="uq_historical_snapshot_game_pk"),
)

historical_report = Table(
    "historical_report", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("run_id", String, nullable=False),
    Column("season", Integer, nullable=False),
    Column("game_pk", Integer, nullable=False),
    Column("game_date", Date, nullable=False),
    Column("manifest_status", String, nullable=False),
    Column("output_hash", String, nullable=True),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("game_pk", "run_id", name="uq_historical_report_identity"),
)

historical_season_run = Table(
    "historical_season_run", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("run_id", String, nullable=False, unique=True),
    Column("season", Integer, nullable=False),
    Column("status", String, nullable=False),  # running / completed / failed
    Column("games_total", Integer, nullable=True),
    Column("games_processed", Integer, nullable=False, default=0),
    Column("games_errors", Integer, nullable=False, default=0),
    Column("started_at", DateTime, nullable=False),
    Column("completed_at", DateTime, nullable=True),
)


def get_engine(database_url: str) -> Engine:
    return create_engine(database_url, future=True)


def init_historical_storage(engine: Engine) -> None:
    metadata.create_all(engine)


def upsert_game(engine: Engine, **fields) -> None:
    with engine.begin() as conn:
        insert_ignore_duplicates(conn, historical_game, recorded_at=datetime.now(timezone.utc), **fields)


def update_game_result(engine: Engine, game_pk: int, home_score: int, away_score: int) -> None:
    winner = "home" if home_score > away_score else "away"
    with engine.begin() as conn:
        conn.execute(
            historical_game.update()
            .where(historical_game.c.game_pk == game_pk)
            .values(home_score=home_score, away_score=away_score, winner=winner)
        )


def persist_historical_snapshot(engine: Engine, *, season: int, game_pk: int, game_date: date, snapshot_hash: str, snapshot_payload: dict) -> None:
    """Guarda el `GameSnapshot` reconstruido junto al reporte -- necesario
    para que `validation.py` pueda correr los modelos legado (Seccion
    12.3) sobre EXACTAMENTE los mismos insumos crudos que uso el Evidence
    Engine, en vez de tener que volver a golpear la API."""
    try:
        with engine.begin() as conn:
            insert_ignore_duplicates(
                conn, historical_snapshot,
                recorded_at=datetime.now(timezone.utc), season=season, game_pk=game_pk, game_date=game_date,
                snapshot_hash=snapshot_hash, payload=snapshot_payload,
            )
    except IntegrityError:
        pass


def snapshots_for_season(engine: Engine, season: int) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(historical_snapshot).where(historical_snapshot.c.season == season)).mappings().all()
    return [dict(r) for r in rows]


def persist_historical_report(engine: Engine, *, run_id: str, season: int, game_pk: int, game_date: date, report_payload: dict) -> None:
    try:
        with engine.begin() as conn:
            insert_ignore_duplicates(
                conn, historical_report,
                recorded_at=datetime.now(timezone.utc), run_id=run_id, season=season, game_pk=game_pk,
                game_date=game_date, manifest_status=report_payload["manifest_status"],
                output_hash=report_payload.get("output_hash"), payload=report_payload,
            )
    except IntegrityError:
        pass


def start_season_run(engine: Engine, run_id: str, season: int, games_total: int | None) -> None:
    with engine.begin() as conn:
        insert_ignore_duplicates(
            conn, historical_season_run,
            recorded_at=datetime.now(timezone.utc), run_id=run_id, season=season, status="running",
            games_total=games_total, games_processed=0, games_errors=0, started_at=datetime.now(timezone.utc),
            completed_at=None,
        )


def finish_season_run(engine: Engine, run_id: str, *, games_processed: int, games_errors: int, status: str = "completed") -> None:
    with engine.begin() as conn:
        conn.execute(
            historical_season_run.update()
            .where(historical_season_run.c.run_id == run_id)
            .values(games_processed=games_processed, games_errors=games_errors, status=status, completed_at=datetime.now(timezone.utc))
        )


def games_for_season(engine: Engine, season: int) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(historical_game).where(historical_game.c.season == season)).mappings().all()
    return [dict(r) for r in rows]


def reports_for_season(engine: Engine, season: int) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(historical_report).where(historical_report.c.season == season)).mappings().all()
    return [dict(r) for r in rows]


def already_ingested_game_pks(engine: Engine, season: int) -> set[int]:
    """Usado por `pipeline.py` para resumibilidad -- una temporada puede
    tomar horas; si la corrida se corta a mitad de camino (ej. timeout de
    GitHub Actions), una re-ejecucion no debe repetir juegos ya evaluados."""
    with engine.connect() as conn:
        rows = conn.execute(select(historical_report.c.game_pk).where(historical_report.c.season == season)).all()
    return {r[0] for r in rows}


def clear_season(engine: Engine, season: int) -> dict:
    """Borra `historical_snapshot`/`historical_report`/`historical_season_run`
    de una temporada -- usado por `run_season_ingestion(..., force=True)`
    para forzar un reproceso completo cuando la logica de reconstruccion
    cambio (ej. campos nuevos del snapshot) y la resumibilidad de
    `already_ingested_game_pks()` saltaria de largo juegos ya evaluados con
    la logica VIEJA. NUNCA borra `historical_game` (schedule/resultados son
    hechos estables, no cambian con la logica de evaluacion)."""
    with engine.begin() as conn:
        snap_result = conn.execute(historical_snapshot.delete().where(historical_snapshot.c.season == season))
        report_result = conn.execute(historical_report.delete().where(historical_report.c.season == season))
        run_result = conn.execute(historical_season_run.delete().where(historical_season_run.c.season == season))
    return {
        "snapshots_deleted": snap_result.rowcount,
        "reports_deleted": report_result.rowcount,
        "season_runs_deleted": run_result.rowcount,
    }
