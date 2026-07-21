"""CLI del Game Flow Research Lab -- separado de `jsa.historical.cli` a
proposito (mismo aislamiento arquitectonico que el resto de
`research_lab/`): `python -m jsa.research_lab.cli closer-leverage-backfill
--db ... --season ...`."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict

from jsa import config as production_config
from jsa.historical import db as historical_db
from jsa.historical.point_in_time_provider import MLBStatsAPIProvider
from jsa.registries import db as registries_db
from jsa.research_lab.hypotheses.closer_leverage.backfill import DEFAULT_LOOKBACK_DAYS, backfill_season
from jsa.research_lab.hypotheses.closer_leverage.evaluate import run_closer_leverage_hypothesis
from jsa.research_lab.registry_sync import append_hypothesis_result

logger = logging.getLogger("jsa.research_lab")


def setup_plain_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Game Flow Research Lab -- CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backfill_parser = subparsers.add_parser(
        "closer-leverage-backfill",
        help=(
            "Re-deriva closer_pitcher_id + IP reciente del cerrador (point-in-time real, requiere red a "
            "statsapi.mlb.com) para cada juego ya ingerido de la temporada, y lo persiste en "
            "historical_closer_leverage. Costo real de red -- ver docstring de backfill.py antes de correr "
            "contra 5 temporadas completas."
        ),
    )
    backfill_parser.add_argument("--db", required=True, help="URL SQLAlchemy de la base historica ya ingerida")
    backfill_parser.add_argument("--season", type=int, required=True, help="Temporada a backfillear")
    backfill_parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS, help=f"Ventana de dias para la IP reciente del cerrador (default: {DEFAULT_LOOKBACK_DAYS})")
    backfill_parser.add_argument("--out", help="Si se indica, tambien escribe el resultado como JSON en esta ruta")

    evaluate_parser = subparsers.add_parser(
        "closer-leverage-evaluate",
        help=(
            "Evalua la hipotesis Closer Leverage contra el baseline real (evidence_score_raw de produccion) "
            "-- requiere haber corrido closer-leverage-backfill antes para las temporadas incluidas. "
            "Si --sync-to-lab-registry, escribe el HypothesisReport en experiment_registry "
            "(decision=retained_in_lab/rejected_no_improvement)."
        ),
    )
    evaluate_parser.add_argument("--db", required=True, help="URL SQLAlchemy de la base historica ya ingerida")
    evaluate_parser.add_argument("--season", action="append", type=int, dest="seasons", required=True, help="Temporada a incluir (repetible)")
    evaluate_parser.add_argument("--sync-to-lab-registry", action="store_true", help="Ademas de reportar, escribe el resultado en experiment_registry")
    evaluate_parser.add_argument("--registries-db", help="URL SQLAlchemy de la base de registries -- cae a config.DATABASE_URL si no se pasa")
    evaluate_parser.add_argument("--out", help="Si se indica, tambien escribe el resultado como JSON en esta ruta")

    args = parser.parse_args()

    if args.command == "closer-leverage-backfill":
        setup_plain_logging()
        engine = historical_db.get_engine(args.db)
        provider = MLBStatsAPIProvider()
        result = backfill_season(engine, provider, args.season, days=args.days)
        output = json.dumps(result, indent=2, default=str)
        print(output)
        if args.out:
            with open(args.out, "w") as f:
                f.write(output)

    elif args.command == "closer-leverage-evaluate":
        setup_plain_logging()
        result = run_closer_leverage_hypothesis(sorted(args.seasons), args.db)
        logger.info("closer-leverage-evaluate completo -- n_games=%s", result.get("n_games"))

        if args.sync_to_lab_registry and "report" in result:
            registries_database_url = args.registries_db or production_config.DATABASE_URL
            engine = registries_db.get_engine(registries_database_url)
            registries_db.init_registries(engine)
            append_hypothesis_result(engine, result["report"], seasons=sorted(args.seasons))
            logger.info(
                "closer-leverage-evaluate: %s -- retained_in_lab=%s (%s)",
                result["report"].hypothesis_id, result["report"].retained_in_lab, result["report"].retention_reason,
            )

        if "report" in result:
            result = {**result, "report": asdict(result["report"])}
        output = json.dumps(result, indent=2, default=str)
        print(output)
        if args.out:
            with open(args.out, "w") as f:
                f.write(output)


if __name__ == "__main__":
    main()
