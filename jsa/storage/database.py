"""Feature Store + Results Store + persistencia de reportes/manifests --
Seccion 4.1-4.2 del spec JSA v3.0.

Feature Store (`game_snapshots`) y Results Store (`results`) son tablas
separadas que nunca se hacen join sin control de fecha de corte (Seccion
4.2) -- en esta entrega comparten el mismo archivo SQLite/Postgres por
simplicidad operativa (igual que `mlb_edge_analyzer.v2`), pero como
namespaces logicos completamente independientes, nunca mezclados en una
misma consulta salvo con corte explicito de fecha.

`persist_run()` guarda snapshot + reporte + manifest en UNA sola
transaccion (leccion de `mlb_edge_analyzer.v2/db/database.py`: 3 commits
separados dejaban corridas a medias si el proceso fallaba entre uno y
otro), con un reintento unico ante `IntegrityError` en carreras de upsert
concurrentes (misma leccion)."""

from __future__ import annotations

import time
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Column, Date, DateTime, Integer, MetaData, String, Table, UniqueConstraint, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from jsa.domain.models import GameSnapshot, JSAReport, RunManifest

metadata = MetaData()

game_snapshots = Table(
    "game_snapshots", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("game_id", String, nullable=False),
    Column("game_pk", Integer, nullable=True),
    Column("game_date", Date, nullable=False),
    Column("season", Integer, nullable=False),
    Column("snapshot_hash", String, nullable=False),
    Column("schema_version", String, nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("game_id", "game_date", "snapshot_hash", name="uq_snapshot_identity"),
)

results = Table(
    "results", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("game_pk", Integer, nullable=False),
    Column("game_date", Date, nullable=False),
    Column("home_score", Integer, nullable=False),
    Column("away_score", Integer, nullable=False),
    Column("winner", String, nullable=False),
    Column("total_runs", Integer, nullable=False),
    UniqueConstraint("game_pk", "game_date", name="uq_result_identity"),
)

run_manifests = Table(
    "run_manifests", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("run_id", String, nullable=False, unique=True),
    Column("invalidated", Boolean, nullable=False),
    Column("invalidation_reasons", JSON, nullable=False, default=list),
    Column("payload", JSON, nullable=False),
)

jsa_reports = Table(
    "jsa_reports", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime, nullable=False),
    Column("run_id", String, nullable=False),
    Column("game_id", String, nullable=False),
    Column("game_date", Date, nullable=False),
    Column("manifest_status", String, nullable=False),
    Column("output_hash", String, nullable=True),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("run_id", "game_id", name="uq_report_identity"),
)


def init_storage(engine: Engine) -> None:
    metadata.create_all(engine)


def persist_run(engine: Engine, snapshot: GameSnapshot, report: JSAReport, manifest: RunManifest) -> None:
    """Snapshot + reporte + manifest en UNA transaccion, con un reintento
    ante `IntegrityError` (ej. dos corridas concurrentes del mismo dia)."""
    for attempt in range(2):
        try:
            with engine.begin() as conn:
                conn.execute(
                    game_snapshots.insert().values(
                        recorded_at=datetime.now(timezone.utc), game_id=snapshot.game_id, game_pk=snapshot.game_pk,
                        game_date=snapshot.game_date, season=snapshot.season, snapshot_hash=snapshot.snapshot_hash,
                        schema_version=snapshot.schema_version, payload=snapshot.model_dump(mode="json"),
                    ).prefix_with("OR IGNORE", dialect="sqlite")
                )
                conn.execute(
                    run_manifests.insert().values(
                        recorded_at=datetime.now(timezone.utc), run_id=manifest.run_id, invalidated=manifest.invalidated,
                        invalidation_reasons=manifest.invalidation_reasons, payload=manifest.model_dump(mode="json"),
                    ).prefix_with("OR IGNORE", dialect="sqlite")
                )
                conn.execute(
                    jsa_reports.insert().values(
                        recorded_at=datetime.now(timezone.utc), run_id=report.run_id, game_id=report.game_id,
                        game_date=report.game_date, manifest_status=report.manifest_status,
                        output_hash=report.output_hash, payload=report.model_dump(mode="json"),
                    ).prefix_with("OR IGNORE", dialect="sqlite")
                )
            return
        except IntegrityError:
            if attempt == 0:
                time.sleep(0.1)
                continue
            raise


def record_result(engine: Engine, game_pk: int, game_date: date, home_score: int, away_score: int) -> None:
    winner = "home" if home_score > away_score else "away"
    try:
        with engine.begin() as conn:
            conn.execute(
                results.insert().values(
                    recorded_at=datetime.now(timezone.utc), game_pk=game_pk, game_date=game_date,
                    home_score=home_score, away_score=away_score, winner=winner, total_runs=home_score + away_score,
                ).prefix_with("OR IGNORE", dialect="sqlite")
            )
    except IntegrityError:
        pass


def reports_for_date(engine: Engine, target_date: date) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(jsa_reports).where(jsa_reports.c.game_date == target_date)).mappings().all()
    return [dict(r) for r in rows]


def manifests_for_date(engine: Engine, target_date: date) -> list[dict]:
    """Usado por el step 'Check if today already ran' del workflow diario
    -- compara juegos elegibles del dia vs. manifests ya guardados hoy."""
    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(jsa_reports.c.run_id, jsa_reports.c.game_id).where(jsa_reports.c.game_date == target_date)
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]
