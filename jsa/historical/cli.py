"""CLI de ingesta historica -- `python -m jsa.historical.cli season 2022`.

Aislado de `jsa/main.py` (produccion en vivo) a proposito -- ver
`tests/test_production_isolation.py`, que ahora tambien verifica que
`main.py` nunca importe nada de `jsa/historical/`."""

from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import asdict
from datetime import date

from jsa.historical.config import SUPPORTED_SEASONS
from jsa.historical.merge import merge_databases
from jsa.historical.monte_carlo import run_monte_carlo_audit
from jsa.historical.pipeline import run_season_ingestion
from jsa.historical.validation import benchmark_season

logger = logging.getLogger("jsa.historical")


def setup_logging(season: int) -> str:
    os.makedirs("jsa/logs", exist_ok=True)
    log_file = f"jsa/logs/jsa_historical_{season}_{date.today().strftime('%Y%m%d')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
    )
    return log_file


def setup_plain_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingesta historica de JSA v3.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    season_parser = subparsers.add_parser("season", help="Ingiere una temporada completa")
    season_parser.add_argument("year", type=int, help=f"Temporada a ingerir, una de {SUPPORTED_SEASONS}")

    merge_parser = subparsers.add_parser("merge", help="Fusiona N bases historicas separadas en una sola")
    merge_parser.add_argument("--source", action="append", required=True, dest="sources", help="URL SQLAlchemy de una base fuente (repetible, una por temporada)")
    merge_parser.add_argument("--target", required=True, help="URL SQLAlchemy de la base destino fusionada")

    validate_parser = subparsers.add_parser("validate", help="Corre benchmark_season() + Monte Carlo Audit sobre una base ya ingerida")
    validate_parser.add_argument("--db", required=True, help="URL SQLAlchemy de la base a validar (tipicamente la fusionada)")
    validate_parser.add_argument("--season", action="append", type=int, dest="seasons", required=True, help="Temporada a validar (repetible)")
    validate_parser.add_argument("--monte-carlo-sims", type=int, default=200, help="Cantidad de simulaciones de Monte Carlo Audit por temporada")
    validate_parser.add_argument("--out", help="Si se indica, tambien escribe el resultado como JSON en esta ruta")

    args = parser.parse_args()

    if args.command == "season":
        if args.year not in SUPPORTED_SEASONS:
            parser.error(f"Temporada {args.year} no soportada -- SUPPORTED_SEASONS={SUPPORTED_SEASONS}")
        setup_logging(args.year)
        summary = run_season_ingestion(args.year)
        print(summary)

    elif args.command == "merge":
        setup_plain_logging()
        counts = merge_databases(args.sources, args.target)
        logger.info("merge_databases completo -- target=%s filas_fusionadas=%s", args.target, counts)
        print(counts)

    elif args.command == "validate":
        setup_plain_logging()
        result = {"seasons": {}}
        for season in args.seasons:
            benchmark = benchmark_season(season, args.db)
            audit = run_monte_carlo_audit(season, args.db, n_simulations=args.monte_carlo_sims)
            result["seasons"][season] = {"benchmark": benchmark, "monte_carlo_audit": asdict(audit)}
            logger.info("validate(%s) completo -- n_games_scored=%s", season, benchmark.get("n_games_scored"))
        output = json.dumps(result, indent=2, default=str)
        print(output)
        if args.out:
            with open(args.out, "w") as f:
                f.write(output)


if __name__ == "__main__":
    main()
