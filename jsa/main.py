"""Orquestador diario de JSA v3.0 -- `python -m jsa.main`.

Hace TODO el trabajo de I/O (red, disco, base de datos) y delega toda la
evaluacion real a `engine.orchestrator.evaluate_game()`, que es pura
(Seccion 2). Aislado por diseno de cualquier motor experimental/historico
que se agregue en el futuro (ver `tests/test_production_isolation.py`)."""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import date

from jsa import config
from jsa.data_sources import injuries, mlb_api, park_factors, travel, weather
from jsa.data_sources.snapshot_builder import build_league_context, build_snapshot_from_game
from jsa.engine.orchestrator import evaluate_game
from jsa.governance import provenance
from jsa.registries import db as registries_db
from jsa.registries.seed import REGISTRY_VERSION, seed_all
from jsa.storage import database as storage_db

logger = logging.getLogger("jsa")


def setup_logging() -> str:
    os.makedirs("jsa/logs", exist_ok=True)
    log_file = f"jsa/logs/jsa_{date.today().strftime('%Y%m%d')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
    )
    return log_file


def run_daily(target_date: date | None = None, is_production: bool = True) -> dict:
    start = time.monotonic()
    target_date = target_date or date.today()
    run_id = str(uuid.uuid4())

    engine = registries_db.get_engine(config.DATABASE_URL)
    seed_all(engine)
    storage_db.init_storage(engine)
    provenance.init_provenance(engine)

    rule_registry_rows = registries_db.latest_by_id(engine, registries_db.rule_registry, "rule_id")
    feature_registry_rows = registries_db.latest_by_id(engine, registries_db.feature_registry, "feature_id")
    pillar_registry_rows = registries_db.latest_by_id(engine, registries_db.pillar_registry, "pillar_id")
    experiment_ids = set(registries_db.latest_by_id(engine, registries_db.experiment_registry, "experiment_id").keys())

    games = mlb_api.get_schedule(target_date)
    logger.info("Schedule de %s: %d juegos en Preview.", target_date, len(games))

    league_context = build_league_context(config.SEASON)
    weather_by_home_team = weather.preload_weather(games, park_factors.get_park_info)
    travel_by_away_team = travel.preload_travel_distances(games, target_date)
    injury_index = injuries.build_today_injury_index(config.SEASON)

    processed, errors, invalidated_count = 0, 0, 0
    for game in games:
        try:
            snapshot = build_snapshot_from_game(
                game, weather_by_home_team, league_context, config.SEASON, travel_by_away_team, injury_index
            )
            report = evaluate_game(
                snapshot, run_id=run_id, model_version=config.MODEL_VERSION,
                rule_registry_rows=rule_registry_rows, feature_registry_rows=feature_registry_rows,
                pillar_registry_rows=pillar_registry_rows, registry_version_tag=REGISTRY_VERSION,
                experiment_ids=experiment_ids, is_production=is_production,
            )

            propagation_warnings = provenance.check_propagated_warnings(engine, [snapshot.snapshot_hash])
            if propagation_warnings:
                report = report.model_copy(update={"warnings": report.warnings + propagation_warnings})

            storage_db.persist_run(engine, snapshot, report, report.manifest)

            node = provenance.build_node(
                node_id=run_id + ":" + snapshot.game_id,
                inputs=[snapshot.snapshot_hash],
                outputs=[report.output_hash or ""],
                version=config.MODEL_VERSION,
            )
            provenance.append_node(engine, node, invalidated=report.manifest.invalidated)

            processed += 1
            if report.manifest.invalidated:
                invalidated_count += 1
                logger.warning(
                    "game_id=%s INVALIDATED: %s", snapshot.game_id, ", ".join(report.manifest.invalidation_reasons)
                )
            else:
                logger.info("game_id=%s evaluado OK: %s", snapshot.game_id, report.one_sentence_explanation)
        except Exception:  # noqa: BLE001 -- aislar fallas por juego, nunca tumbar el resto del dia
            errors += 1
            logger.exception("Error inesperado procesando game_pk=%s", game.get("game_pk"))

    elapsed = time.monotonic() - start
    logger.info(
        "run_daily() completo en %.1fs -- juegos=%d procesados=%d invalidados=%d errores=%d run_id=%s",
        elapsed, len(games), processed, invalidated_count, errors, run_id,
    )
    return {"run_id": run_id, "games": len(games), "processed": processed, "invalidated": invalidated_count, "errors": errors}


def update_results(lookback_days: int = 5) -> int:
    """Trae resultados finales de juegos evaluados en los ultimos
    `lookback_days` dias y los guarda en el Results Store (Seccion 4.2) --
    separado del Feature Store, nunca mezclado sin control de fecha."""
    from datetime import timedelta

    engine = registries_db.get_engine(config.DATABASE_URL)
    storage_db.init_storage(engine)

    recorded = 0
    for offset in range(1, lookback_days + 1):
        target = date.today() - timedelta(days=offset)
        for row in storage_db.reports_for_date(engine, target):
            game_pk = row.get("payload", {}).get("game_pk") if isinstance(row.get("payload"), dict) else None
            if not game_pk:
                continue
            result = mlb_api.get_game_result(game_pk)
            if result is None:
                continue
            storage_db.record_result(engine, game_pk, target, result["home_score"], result["away_score"])
            recorded += 1
    return recorded


if __name__ == "__main__":
    setup_logging()
    summary = run_daily()
    print(summary)
