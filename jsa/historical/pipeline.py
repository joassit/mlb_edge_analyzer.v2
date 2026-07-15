"""Orquesta la ingesta historica de una temporada completa: por cada
juego `Final`, reconstruye un `GameSnapshot` punto-en-el-tiempo y lo
evalua con **exactamente la misma funcion pura que produccion en vivo**
(`jsa.engine.orchestrator.evaluate_game()`, sin ninguna copia ni
reimplementacion) -- la confirmacion en la practica del objetivo de
diseño original: una unica funcion de evaluacion, reusada sin cambios en
vivo y en backtest.

Lee los Registries (Feature/Rule/Pillar/Market -- metadata compartida del
sistema, Seccion 2 del spec) de la MISMA base que produccion
(`jsa.config.DATABASE_URL`), en modo solo-lectura: nunca escribe una fila
de registry nueva desde aqui. Los datos de juego (snapshots, reportes)
SI van a su propia base completamente separada
(`historical/config.HISTORICAL_DATABASE_URL`) -- mismo aislamiento que
`historical_engine/db.py` en el proyecto hermano."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import date

from jsa import config as production_config
from jsa.engine.orchestrator import evaluate_game
from jsa.historical import config as historical_config
from jsa.historical import db as historical_db
from jsa.historical.ingestion import build_previous_park_index, fetch_season_games
from jsa.historical.injuries import build_injury_index, fetch_season_transactions, parse_il_events
from jsa.historical.point_in_time_provider import HistoricalStatsProvider, MLBStatsAPIProvider
from jsa.historical.snapshot_reconstruction import reconstruct_snapshot
from jsa.registries import db as registries_db
from jsa.registries.seed import REGISTRY_VERSION, seed_all

logger = logging.getLogger("jsa.historical")


def run_season_ingestion(
    season: int,
    provider: HistoricalStatsProvider | None = None,
    *,
    historical_database_url: str | None = None,
    registries_database_url: str | None = None,
) -> dict:
    """`historical_database_url`/`registries_database_url` son parametros
    explicitos (no solo constantes de modulo) para que un test pueda
    aislar la corrida sin depender de parchear un nombre ya importado --
    caen a `historical.config.HISTORICAL_DATABASE_URL`/
    `config.DATABASE_URL` si no se pasan, que es el comportamiento real en
    produccion (`cli.py` nunca los pasa)."""
    start = time.monotonic()
    run_id = str(uuid.uuid4())
    provider = provider or MLBStatsAPIProvider()
    historical_database_url = historical_database_url or historical_config.HISTORICAL_DATABASE_URL
    registries_database_url = registries_database_url or production_config.DATABASE_URL

    historical_engine = historical_db.get_engine(historical_database_url)
    historical_db.init_historical_storage(historical_engine)

    registries_engine = registries_db.get_engine(registries_database_url)
    # Idempotente (ver registries/seed.py) -- la ingesta historica puede
    # correr antes de que jsa/main.py haya corrido ni una vez contra esta
    # base (ej. una base de produccion nueva), nunca debe asumir que los
    # registries ya existen.
    seed_all(registries_engine)
    rule_registry_rows = registries_db.latest_by_id(registries_engine, registries_db.rule_registry, "rule_id")
    feature_registry_rows = registries_db.latest_by_id(registries_engine, registries_db.feature_registry, "feature_id")
    pillar_registry_rows = registries_db.latest_by_id(registries_engine, registries_db.pillar_registry, "pillar_id")
    experiment_ids = set(registries_db.latest_by_id(registries_engine, registries_db.experiment_registry, "experiment_id").keys())

    games = fetch_season_games(season)
    previous_park_index = build_previous_park_index(games)
    # Trae TODA la temporada de transacciones una sola vez (igual que el
    # schedule) -- build_injury_index() si pega la red, pero una vez POR
    # JUGADOR lesionado en la temporada, nunca por juego (ver injuries.py).
    il_events = parse_il_events(fetch_season_transactions(season))
    injury_index = build_injury_index(il_events, provider)
    already_done = historical_db.already_ingested_game_pks(historical_engine, season)
    pending = [g for g in games if g["game_pk"] not in already_done]
    logger.info(
        "Temporada %s: %d juegos totales, %d ya ingeridos (se saltan), %d pendientes.",
        season, len(games), len(already_done), len(pending),
    )

    historical_db.start_season_run(historical_engine, run_id, season, games_total=len(games))

    processed, errors = 0, 0
    for game in pending:
        try:
            # ingestion.py entrega game_date como string ISO (tal cual la API) --
            # las columnas SQLAlchemy Date exigen un date real, a diferencia de
            # Pydantic (GameSnapshot), que coerciona el string solo.
            game_date_obj = date.fromisoformat(game["game_date"])
            historical_db.upsert_game(
                historical_engine, season=season, game_pk=game["game_pk"], game_date=game_date_obj,
                home_team=game["home_team"], away_team=game["away_team"], home_team_id=game["home_team_id"],
                away_team_id=game["away_team_id"], home_pitcher_id=game["home_pitcher_id"],
                away_pitcher_id=game["away_pitcher_id"], is_double_header=int(game["is_double_header"]),
            )
            historical_db.update_game_result(historical_engine, game["game_pk"], game["home_score"], game["away_score"])

            snapshot = reconstruct_snapshot(
                game_pk=game["game_pk"], game_date=game["game_date"], season=season,
                home_team=game["home_team"], away_team=game["away_team"],
                home_team_id=game["home_team_id"], away_team_id=game["away_team_id"],
                home_pitcher_id=game["home_pitcher_id"], away_pitcher_id=game["away_pitcher_id"],
                is_double_header=game["is_double_header"], provider=provider,
                away_team_previous_park_id=previous_park_index.get((game["away_team_id"], game["game_pk"])),
                injury_index=injury_index,
            )

            report = evaluate_game(
                snapshot, run_id=run_id, model_version=production_config.MODEL_VERSION,
                rule_registry_rows=rule_registry_rows, feature_registry_rows=feature_registry_rows,
                pillar_registry_rows=pillar_registry_rows, registry_version_tag=REGISTRY_VERSION,
                experiment_ids=experiment_ids, is_production=False,
            )

            historical_db.persist_historical_snapshot(
                historical_engine, season=season, game_pk=game["game_pk"], game_date=snapshot.game_date,
                snapshot_hash=snapshot.snapshot_hash, snapshot_payload=snapshot.model_dump(mode="json"),
            )
            historical_db.persist_historical_report(
                historical_engine, run_id=run_id, season=season, game_pk=game["game_pk"],
                game_date=snapshot.game_date, report_payload=report.model_dump(mode="json"),
            )
            processed += 1
        except Exception:  # noqa: BLE001 -- aislar fallas por juego, nunca tumbar la temporada completa
            errors += 1
            logger.exception("Error inesperado procesando game_pk=%s de la temporada %s", game.get("game_pk"), season)

    historical_db.finish_season_run(historical_engine, run_id, games_processed=processed, games_errors=errors)

    elapsed = time.monotonic() - start
    logger.info(
        "run_season_ingestion(%s) completo en %.1fs -- total=%d pendientes=%d procesados=%d errores=%d run_id=%s",
        season, elapsed, len(games), len(pending), processed, errors, run_id,
    )
    return {
        "run_id": run_id, "season": season, "games_total": len(games), "already_ingested": len(already_done),
        "processed": processed, "errors": errors,
    }
