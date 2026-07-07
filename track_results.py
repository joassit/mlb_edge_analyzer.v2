"""
Actualiza resultados reales de juegos pasados y muestra el desempeño
del modelo hasta ahora.

Uso:
    python track_results.py

Corre esto una vez al día (idealmente antes de `python main.py`, para
que las predicciones de ayer ya tengan su resultado guardado).
"""

from logging_config import setup_logging
from db.database import init_db
from tracking.results_tracker import update_results, print_performance_report, print_calibration_report

if __name__ == "__main__":
    logger = setup_logging()
    logger.info("Actualizando resultados reales")

    init_db()
    updated = update_results(days_back=5)
    print(f"Resultados actualizados: {updated} juego(s)\n")

    print_performance_report(days=30)
    print_calibration_report(days=90)

    logger.info(f"Tracking terminado: {updated} resultados actualizados")
