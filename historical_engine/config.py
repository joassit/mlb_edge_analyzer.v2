"""
Configuración propia de historical_engine -- deliberadamente NO importa
nada de config.py (el config de producción) salvo constantes puramente
de solo-lectura sin estado (MLB_API_BASE: una URL, no un objeto con
estado ni una caché). Todo lo demás (URL de base de datos, temporadas
soportadas, umbrales de validación) vive acá, separado, para que un
cambio futuro en config.py nunca pueda alterar el comportamiento del
motor histórico por accidente.
"""

import os

from config import MLB_API_BASE  # solo una constante de URL, sin estado

# Temporadas soportadas para backtesting -- nunca se mezclan automáticamente
# entre sí (ver historical_engine/pipeline.py, cada corrida pertenece a UNA
# sola temporada). Se puede ampliar sin tocar el resto del motor.
SUPPORTED_SEASONS = [2023, 2024, 2025, 2026]

# Base de datos COMPLETAMENTE separada de mlb_edge.db -- archivo físico
# distinto, nunca el mismo motor de conexión que db/database.py. Variable
# de entorno propia (HISTORICAL_DATABASE_URL), nunca DATABASE_URL (esa es
# la de producción) -- si se comparte la variable por error de config, un
# valor SQLite apuntaría al mismo archivo, algo que queremos hacer
# estructuralmente imposible por default.
HISTORICAL_DATABASE_URL = os.getenv(
    "HISTORICAL_DATABASE_URL", "sqlite:///historical_backtest.db"
)

# Jerarquía explícita (ver Historical Confidence Engine y pipeline):
# producción > temporada actual > histórico. CURRENT_SEASON marca cuál
# temporada NUNCA debe tratarse como "histórico cerrado" al comparar --
# sigue en curso, su muestra crece cada día vía el pipeline de producción,
# no vía este motor.
CURRENT_SEASON = int(os.getenv("MLB_SEASON", "2026"))

# Techo de reintentos/paralelismo para ingesta histórica -- deliberadamente
# bajo (a diferencia de producción) porque una corrida de backtesting
# barre cientos de juegos y no debe golpear la MLB Stats API de forma
# agresiva.
INGESTION_MAX_WORKERS = 4
INGESTION_REQUEST_TIMEOUT = 20

# Umbral de muestra mínima para que cualquier métrica de
# historical_engine/validation.py se reporte sin advertencia de "muestra
# insuficiente" -- deliberadamente el mismo criterio de rigor que
# config.MIN_LIQUIDATED_PICKS_FOR_CALIBRATION del sistema de producción,
# pero como constante propia (no se importa de ahí) para que este módulo
# nunca dependa de que ese valor no cambie.
MIN_SAMPLE_FOR_VALIDATION = 200

__all__ = [
    "MLB_API_BASE",
    "SUPPORTED_SEASONS",
    "HISTORICAL_DATABASE_URL",
    "CURRENT_SEASON",
    "INGESTION_MAX_WORKERS",
    "INGESTION_REQUEST_TIMEOUT",
    "MIN_SAMPLE_FOR_VALIDATION",
]
