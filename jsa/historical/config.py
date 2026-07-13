"""Configuracion propia de `jsa/historical/` -- deliberadamente separada
de `jsa/config.py` (produccion en vivo) y de cualquier config del proyecto
hermano `mlb_edge_analyzer.v2`. Mismo criterio de aislamiento que ya
probo `historical_engine/config.py` en el proyecto viejo: un motor de
backtesting nunca debe poder alterar produccion por compartir una
variable o una constante con estado."""

from __future__ import annotations

import os

# Base de datos COMPLETAMENTE separada de jsa.db (produccion) -- variable
# de entorno propia, nunca JSA_DATABASE_URL. Si se comparte por error de
# config, un valor SQLite apuntaria al mismo archivo -- lo hacemos
# estructuralmente imposible por default (archivo distinto).
#
# `os.getenv(key) or default`, no `os.getenv(key, default)`: la segunda
# forma solo aplica el default cuando la variable esta AUSENTE, no cuando
# existe pero esta VACIA -- y un workflow que asigna
# `env: X: ${{ secrets.X }}` sin el secret configurado exporta justamente
# eso. Le tumbo la primera corrida real de esta ingesta (season 2022, run
# 29260616665, "Could not parse SQLAlchemy URL from given URL string")
# antes de que se corrigiera este patron aqui y en jsa_historical_ingest.yml.
HISTORICAL_DATABASE_URL = os.getenv("JSA_HISTORICAL_DATABASE_URL") or "sqlite:///jsa_historical.db"

# Mismo rango que historical_engine/config.py::SUPPORTED_SEASONS del
# proyecto hermano -- coincidencia deliberada (mismo universo de datos
# util), no una dependencia tecnica (este archivo no lo importa).
SUPPORTED_SEASONS: list[int] = [2022, 2023, 2024, 2025, 2026]

# La temporada en curso nunca se trata como "historico cerrado": su
# muestra crece cada dia. La ingesta de CURRENT_SEASON se acota siempre a
# juegos ya `Final` a la fecha de la corrida (ver pipeline.py).
CURRENT_SEASON = int(os.getenv("JSA_SEASON") or "2026")

# Deliberadamente mas bajo que produccion: una corrida de ingesta
# historica barre cientos/miles de juegos y no debe golpear la MLB Stats
# API de forma agresiva.
INGESTION_REQUEST_TIMEOUT = 20
INGESTION_MAX_WORKERS = 4

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"
