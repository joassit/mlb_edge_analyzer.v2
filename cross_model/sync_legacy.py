"""Sincroniza las predicciones REALES del modelo MLB legado (`picks`,
`actual_results` de `db/database.py`) hacia `unified_model_predictions`.

Solo LEE de `db.database` -- nunca llama a `save_analysis()`/
`save_picks()`/`settle_picks_for_game()` ni ninguna otra funcion de
escritura de ese modulo, y nunca usa `db.database.SessionLocal` (que
queda atado al `DATABASE_URL` que existiera al importar el modulo por
primera vez) -- crea su PROPIO engine/sesion a partir de la URL que se le
pasa explicitamente, exactamente el mismo patron que ya usa
`tests/test_historical_isolation.py::_seed_production_db()` para probar
el aislamiento del motor historico. Esto es un CONSUMIDOR de datos ya
persistidos, nunca un escritor de produccion.

Solo se sincronizan picks de mercado `moneyline` (unico mercado
directamente comparable con las predicciones de JSA/Game Flow, que
predicen home/away, no over/under ni cubrir un spread)."""

from __future__ import annotations

import argparse
import logging
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.database as production_db

from cross_model import config as cross_model_config
from cross_model import db as unified_db

logger = logging.getLogger("cross_model")

LEGACY_SYSTEM = "mlb_legacy"


def _home_win_prob(selection: str, model_prob: float) -> float | None:
    if selection == "home":
        return model_prob
    if selection == "away":
        return 1.0 - model_prob
    return None


def sync_legacy_moneyline_picks(legacy_db_url: str, unified_db_url: str) -> int:
    legacy_engine = create_engine(legacy_db_url, future=True)
    LegacySession = sessionmaker(bind=legacy_engine)
    unified_engine = unified_db.get_engine(unified_db_url)
    unified_db.init_storage(unified_engine)

    session = LegacySession()
    try:
        actual_winners = {r.game_pk: r.winner for r in session.query(production_db.ActualResult).all()}
        picks = session.query(production_db.Pick).filter(production_db.Pick.market == "moneyline").all()

        n = 0
        for p in picks:
            home_prob = _home_win_prob(p.selection, p.model_prob)
            if home_prob is None:
                continue  # selection ajeno a home/away (no deberia pasar en moneyline, pero nunca se asume)
            game_date_parsed = date.fromisoformat(p.game_date)
            unified_db.upsert_prediction(
                unified_engine, game_pk=p.game_pk, game_date=game_date_parsed, season=game_date_parsed.year,
                system=LEGACY_SYSTEM, model_name=f"legacy_{p.prob_source or 'unknown'}",
                model_version=p.model_version or "unknown",
                raw_score=home_prob - 0.5, home_win_prob=home_prob, predicted_winner=p.selection,
                actual_winner=actual_winners.get(p.game_pk), source_ref="db.picks",
            )
            n += 1
        logger.info("sync_legacy_moneyline_picks completo -- n_picks=%d", n)
        return n
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sincroniza picks moneyline del modelo legado hacia unified_model_predictions")
    parser.add_argument("--legacy-db", required=True, help="URL SQLAlchemy de la base de produccion del modelo legado (db/database.py)")
    parser.add_argument("--unified-db", default=cross_model_config.UNIFIED_DATABASE_URL, help="URL SQLAlchemy destino (default: UNIFIED_DATABASE_URL)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sync_legacy_moneyline_picks(args.legacy_db, args.unified_db)


if __name__ == "__main__":
    main()
