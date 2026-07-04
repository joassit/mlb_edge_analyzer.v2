"""
Configuración central de MLB Edge Analyzer.
Todo lo que cambie entre entornos (temporada, base de datos, etc.) vive aquí.
"""

import os

# Temporada activa
SEASON = int(os.getenv("MLB_SEASON", "2026"))

# MLB Stats API (oficial, gratuita, sin API key)
MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

# Base de datos:
# - Si defines DATABASE_URL (ej. postgres://user:pass@host:5432/db) se usa PostgreSQL.
# - Si no, cae a un archivo SQLite local (cero configuración, funciona de inmediato).
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///mlb_edge.db")

# Peso del abridor vs. el bullpen dentro del score de pitcheo del equipo.
# 0.65 = 65% abridor, 35% bullpen. Súbelo si crees que el abridor pesa más
# (juegos donde llega a la 6ta-7ma entrada), bájalo si el equipo depende
# mucho de su relevo.
STARTER_WEIGHT = 0.65

# Ventaja de jugar en casa. Basado en que, históricamente, los equipos
# locales en MLB ganan ~54% de los juegos. Se suma directo a la probabilidad
# cruda del equipo local antes de normalizar.
HOME_FIELD_ADVANTAGE = 0.02

# Versión del modelo actual — se guarda con cada predicción para poder
# comparar rendimiento entre versiones más adelante (Fase 4 del roadmap).
MODEL_VERSION = "0.3.0-fase2a-skellam"

# ERA de bullpen a usar si no se puede calcular el real (equipo sin datos
# suficientes, roster incompleto, etc.) — aproximado al promedio de liga.
FALLBACK_BULLPEN_ERA = 4.30

# OPS de liga: mínimo de turnos al bate para considerar a un bateador "calificado"
# al calcular el promedio de liga (evita que bateadores con 3 turnos distorsionen el promedio)
MIN_PA_FOR_LEAGUE_OPS = 100
