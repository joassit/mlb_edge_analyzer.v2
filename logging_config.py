"""
Configuración de logging del proyecto.

- Todo (DEBUG en adelante) se guarda en logs/mlb_edge_YYYYMMDD.log
- Solo advertencias y errores se muestran en consola (para no ensuciar
  el reporte diario, que usa print() directo porque es el "producto",
  no un log de depuración)
"""

import logging
import os
from datetime import date

_configured = False


def setup_logging(log_dir: str = "logs") -> logging.Logger:
    global _configured

    logger = logging.getLogger("mlb_edge_analyzer")

    if _configured:
        return logger

    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"mlb_edge_{date.today():%Y%m%d}.log")

    logger.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    _configured = True
    return logger
