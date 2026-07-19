"""Sincroniza predicciones de JSA (`evidence_score_raw` historico) y del
Game Flow Engine v1.0 Etapa 1 (GF1/GF2) hacia `unified_model_predictions`.

Solo LEE de `jsa/historical/db.py` -- nunca escribe ahi, nunca importa
`jsa/storage/database.py` (produccion en vivo) ni `jsa/main.py`. Mismo
principio de aislamiento que ya protege a `jsa/historical/` en si mismo
(ver `jsa/tests/test_production_isolation.py`): este sync es un
CONSUMIDOR de datos ya persistidos, nunca un escritor de produccion.

Re-ejecutable: cada llamada hace upsert por
`(game_pk, system, model_name, model_version)` -- correrlo de nuevo tras
una re-ingesta no duplica filas, solo actualiza valores."""

from __future__ import annotations

import argparse
import logging

from jsa.historical import db as historical_db
from jsa.historical.discriminative_audit import load_game_pillar_data
from jsa.historical.game_flow_candidate_audit import ALL_HYPOTHESES, load_records_with_game_flow_candidates

from cross_model import config as cross_model_config
from cross_model import db as unified_db

logger = logging.getLogger("cross_model")

JSA_SYSTEM = "jsa"
JSA_EVIDENCE_MODEL_NAME = "evidence_score_raw"
JSA_EVIDENCE_MODEL_VERSION = "jsa-v3.0-historical-backtest"

GAME_FLOW_SYSTEM = "game_flow"
GAME_FLOW_MODEL_VERSION = "game_flow_v1_etapa1"


def _predicted_winner_from_score(score: float | None) -> str | None:
    if score is None or score == 0:
        return None
    return "home" if score > 0 else "away"


def _actual_winner(home_win: int) -> str:
    return "home" if home_win == 1 else "away"


def _game_dates_by_pk(engine, seasons: list[int]) -> dict[int, "object"]:
    dates: dict[int, object] = {}
    for season in seasons:
        for g in historical_db.games_for_season(engine, season):
            dates[g["game_pk"]] = g["game_date"]
    return dates


def sync_jsa_evidence_score(jsa_historical_db_url: str, unified_db_url: str, seasons: list[int]) -> int:
    """Sincroniza `evidence_score_raw` (el score real de produccion de
    JSA, tal como quedo persistido en `historical_report`) por juego.
    Junta todas las filas en memoria y las escribe con
    `upsert_predictions_bulk()` -- un solo lote de transacciones en vez
    de una por juego (relevante contra Postgres remoto, ver
    `cross_model/db.py`)."""
    hist_engine = historical_db.get_engine(jsa_historical_db_url)
    unified_engine = unified_db.get_engine(unified_db_url)
    unified_db.init_storage(unified_engine)

    game_dates = _game_dates_by_pk(hist_engine, seasons)
    records = load_game_pillar_data(hist_engine, seasons)
    rows = []
    for r in records:
        game_date = game_dates.get(r["game_pk"])
        if game_date is None:
            continue
        rows.append(dict(
            game_pk=r["game_pk"], game_date=game_date, season=r["season"],
            system=JSA_SYSTEM, model_name=JSA_EVIDENCE_MODEL_NAME, model_version=JSA_EVIDENCE_MODEL_VERSION,
            raw_score=r["evidence_score_raw"], home_win_prob=None,
            predicted_winner=_predicted_winner_from_score(r["evidence_score_raw"]),
            actual_winner=_actual_winner(r["home_win"]), source_ref="jsa_historical.historical_report",
        ))
    n = unified_db.upsert_predictions_bulk(unified_engine, rows)
    logger.info("sync_jsa_evidence_score completo -- n_games=%d", n)
    return n


def sync_game_flow_candidates(jsa_historical_db_url: str, unified_db_url: str, seasons: list[int]) -> int:
    """Sincroniza GF1/GF2 (el diff crudo de cada hipotesis, ver
    `jsa/docs/game_flow_design.md`) por juego -- una fila por hipotesis
    con cobertura (`diff is not None`) en ese juego. Mismo criterio de
    lote que `sync_jsa_evidence_score()`."""
    hist_engine = historical_db.get_engine(jsa_historical_db_url)
    unified_engine = unified_db.get_engine(unified_db_url)
    unified_db.init_storage(unified_engine)

    game_dates = _game_dates_by_pk(hist_engine, seasons)
    records = load_records_with_game_flow_candidates(hist_engine, seasons)
    rows = []
    for r in records:
        game_date = game_dates.get(r["game_pk"])
        if game_date is None:
            continue
        actual_winner = _actual_winner(r["home_win"])
        for hyp in ALL_HYPOTHESES:
            diff = r["game_flow_diffs"][hyp]
            if diff is None:
                continue
            rows.append(dict(
                game_pk=r["game_pk"], game_date=game_date, season=r["season"],
                system=GAME_FLOW_SYSTEM, model_name=hyp, model_version=GAME_FLOW_MODEL_VERSION,
                raw_score=diff, home_win_prob=None, predicted_winner=_predicted_winner_from_score(diff),
                actual_winner=actual_winner, source_ref="jsa_historical.game_flow_candidate_audit",
            ))
    n = unified_db.upsert_predictions_bulk(unified_engine, rows)
    logger.info("sync_game_flow_candidates completo -- n_rows=%d", n)
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="Sincroniza predicciones de JSA/Game Flow hacia unified_model_predictions")
    parser.add_argument("--jsa-historical-db", required=True, help="URL SQLAlchemy de la base historica de JSA ya ingerida")
    parser.add_argument("--unified-db", default=cross_model_config.UNIFIED_DATABASE_URL, help="URL SQLAlchemy destino (default: UNIFIED_DATABASE_URL)")
    parser.add_argument("--season", action="append", type=int, dest="seasons", required=True, help="Temporada a sincronizar (repetible)")
    parser.add_argument("--skip-evidence-score", action="store_true", help="No sincronizar evidence_score_raw de JSA")
    parser.add_argument("--skip-game-flow", action="store_true", help="No sincronizar GF1/GF2 del Game Flow Engine")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not args.skip_evidence_score:
        sync_jsa_evidence_score(args.jsa_historical_db, args.unified_db, sorted(args.seasons))
    if not args.skip_game_flow:
        sync_game_flow_candidates(args.jsa_historical_db, args.unified_db, sorted(args.seasons))


if __name__ == "__main__":
    main()
