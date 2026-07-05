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
MODEL_VERSION = "0.5.0-fase0-modelo-reconectado"

# ERA de bullpen a usar si no se puede calcular el real (equipo sin datos
# suficientes, roster incompleto, etc.) — aproximado al promedio de liga.
FALLBACK_BULLPEN_ERA = 4.30

# OPS de liga: mínimo de turnos al bate para considerar a un bateador "calificado"
# al calcular el promedio de liga (evita que bateadores con 3 turnos distorsionen el promedio)
MIN_PA_FOR_LEAGUE_OPS = 100
# --- AJUSTES DE PRECISIÓN DE MODELO ---

# Multiplicador para el factor de parque.
# 1.0 es el valor neutral que viene de la API.
# > 1.0 amplifica el efecto (ej. 1.15 = 15% más impacto del estadio en el score).
PARK_FACTOR_WEIGHT = 1.15

# Factor de corrección por clima (temp_f > 85°F).
# Ayuda a mitigar errores en días de calor extremo donde la bola viaja más.
WEATHER_CORRECTION = 0.05

# --- The Odds API: protección de presupuesto ---
# El free tier de The Odds API es ~500 requests/mes — el más limitado de
# todas las APIs que usa este proyecto (MLB Stats API y Open-Meteo no
# tienen ese techo). Estos límites evitan quemar la cuota por refrescos
# repetidos del dashboard o corridas manuales del pipeline el mismo día.
ODDS_API_CACHE_TTL_SECONDS = int(os.getenv("ODDS_API_CACHE_TTL_SECONDS", "900"))  # 15 min
ODDS_API_MONTHLY_BUDGET = int(os.getenv("ODDS_API_MONTHLY_BUDGET", "500"))
ODDS_CACHE_DIR = os.getenv("ODDS_CACHE_DIR", ".cache/odds")

# Umbral de edge (en probabilidad) a partir del cual un juego se marca como
# "candidato a revisión" en el reporte — solo si además los dos modelos
# (heurístico y Skellam) coinciden en el favorito. Es una preselección
# para que decidas tú, nunca una apuesta automática.
REVIEW_EDGE_THRESHOLD = float(os.getenv("REVIEW_EDGE_THRESHOLD", "0.03"))

# --- Picks recomendados (moneyline / run_line / totals) ---
# Un candidato es "viable" si su EV o su edge superan estos umbrales
# (criterio OR, no AND). Si ningún mercado es viable y FORCE_AT_LEAST_ONE_PICK
# está activo, se genera igual el menos malo, marcado forced=True — nunca se
# mezcla con los picks reales en las métricas de desempeño.
MIN_PICK_EV = float(os.getenv("MIN_PICK_EV", "0.05"))
MIN_PICK_EDGE = float(os.getenv("MIN_PICK_EDGE", "0.04"))
FORCE_AT_LEAST_ONE_PICK = os.getenv("FORCE_AT_LEAST_ONE_PICK", "true").lower() == "true"
MAX_PICKS_PER_GAME = int(os.getenv("MAX_PICKS_PER_GAME", "3"))