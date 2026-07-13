"""CLI de ingesta historica -- `python -m jsa.historical.cli season 2022`.

Aislado de `jsa/main.py` (produccion en vivo) a proposito -- ver
`tests/test_production_isolation.py`, que ahora tambien verifica que
`main.py` nunca importe nada de `jsa/historical/`."""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date

from jsa.historical.config import SUPPORTED_SEASONS
from jsa.historical.pipeline import run_season_ingestion

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingesta historica de JSA v3.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    season_parser = subparsers.add_parser("season", help="Ingiere una temporada completa")
    season_parser.add_argument("year", type=int, help=f"Temporada a ingerir, una de {SUPPORTED_SEASONS}")

    args = parser.parse_args()

    if args.command == "season":
        if args.year not in SUPPORTED_SEASONS:
            parser.error(f"Temporada {args.year} no soportada -- SUPPORTED_SEASONS={SUPPORTED_SEASONS}")
        setup_logging(args.year)
        summary = run_season_ingestion(args.year)
        print(summary)


if __name__ == "__main__":
    main()
