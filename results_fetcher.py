"""
Trae resultados reales de juegos ya jugados desde la MLB Stats API
(statsapi.mlb.com, sin API key) y los guarda para las predicciones que
todavía no tienen resultado registrado.

No toca ni borra filas de game_analysis: los resultados se guardan en la
tabla separada actual_results (game_pk, game_date, home_score, away_score,
winner, total_runs), unida a game_analysis por game_pk al momento de
evaluar. Esta tabla y la lógica de fetch ya existían en
tracking/results_tracker.py::update_results() — este script es solo el
punto de entrada por línea de comandos.

Uso:
    python results_fetcher.py [--days-back N]
"""

import argparse

from logging_config import setup_logging
from db.database import init_db
from tracking.results_tracker import update_results


def main():
    parser = argparse.ArgumentParser(description="Sincroniza resultados reales de MLB Stats API")
    parser.add_argument("--days-back", type=int, default=5,
                         help="Cuántos días hacia atrás buscar predicciones sin resultado (default: 5)")
    args = parser.parse_args()

    logger = setup_logging()
    init_db()

    updated = update_results(days_back=args.days_back)
    print(f"Resultados actualizados: {updated} juego(s)")
    logger.info(f"results_fetcher: {updated} resultado(s) sincronizado(s)")


if __name__ == "__main__":
    main()
