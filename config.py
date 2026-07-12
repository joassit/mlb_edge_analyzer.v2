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
MODEL_VERSION = "0.5.0-reconectado"

# ERA de bullpen a usar si no se puede calcular el real (equipo sin datos
# suficientes, roster incompleto, etc.) — aproximado al promedio de liga.
FALLBACK_BULLPEN_ERA = 4.30

# OPS de liga: mínimo de turnos al bate para considerar a un bateador "calificado"
# al calcular el promedio de liga (evita que bateadores con 3 turnos distorsionen el promedio)
MIN_PA_FOR_LEAGUE_OPS = 100
# --- AJUSTES DE PRECISIÓN DE MODELO ---

# Multiplicador para el factor de parque. 1.0 es neutral (el park factor de
# la API se aplica tal cual, sin amplificar). Antes era 1.15 (15% más
# impacto del estadio) -- un valor elegido a mano, sin backtest que lo
# respalde. Se neutraliza a 1.0 hasta que haya suficiente historial en
# feature_snapshots para justificar amplificarlo con evidencia real.
PARK_FACTOR_WEIGHT = 1.0

# Factor de corrección por clima (temp_f > 85°F). Antes era 0.05 (+5% de
# carreras en días de calor extremo) -- mismo caso que PARK_FACTOR_WEIGHT:
# un valor plausible pero sin validar. Neutralizado a 0.0 hasta backtest.
WEATHER_CORRECTION = 0.0

# --- Binomial Negativo: dispersión de carreras ---
# Parámetro k de model/negbin_model.py (varianza = mu + mu^2/k). Entre más
# chico, más sobredispersión (cola gorda) vs. Poisson/Skellam. Se calibra
# por máxima verosimilitud contra resultados reales (ver
# scripts/calibrate_dispersion.py) en cuanto haya ~100 juegos con resultado
# en la base de datos.
#
# Valor actual: 7.0 -- PRIOR de literatura sabermétrica, sin calibrar
# todavía contra datos propios (0 juegos con resultado real disponibles al
# momento de fijar esto). k entre 5 y 10 es el rango típico reportado para
# carreras por equipo por juego en MLB. Recalibrar corriendo
# scripts/calibrate_dispersion.py apenas se acumulen suficientes resultados,
# y actualizar este comentario con la fecha y el número de juegos usados.
NEGBIN_DISPERSION = float(os.getenv("NEGBIN_DISPERSION", "7.0"))

# --- Fuente de probabilidad para picks de moneyline ---
# Qué modelo alimenta la probabilidad de moneyline en model/picks.py:
# "heuristic" (ERA/OPS), "skellam" (Poisson) o "negbin" (Binomial Negativo).
# Cambia SOLO moneyline -- run_line/totals siempre se calculan a partir de
# las carreras proyectadas (Skellam/NB2), nunca del heurístico, así que no
# tienen una fuente configurable que cambiar.
#
# Por qué "skellam" y no "heuristic": el heurístico calcula una probabilidad
# de victoria a partir de ERA/OPS de forma independiente de las carreras
# proyectadas: es una segunda opinión, útil para el chequeo de acuerdo entre
# modelos, pero Skellam es el que efectivamente modela el marcador (de ahí
# sale el total y el run line también) -- generar el pick de moneyline desde
# un modelo distinto al que ya se usa para los otros dos mercados dejaba al
# heurístico corriendo la mesa de decisión de apuesta sin ninguna razón
# consistente frente a los otros dos picks del mismo partido.
PICK_PROBABILITY_SOURCE = os.getenv("PICK_PROBABILITY_SOURCE", "skellam")

# --- The Odds API: protección de presupuesto ---
# El free tier de The Odds API es ~500 requests/mes — el más limitado de
# todas las APIs que usa este proyecto (MLB Stats API y Open-Meteo no
# tienen ese techo). Estos límites evitan quemar la cuota por refrescos
# repetidos del dashboard o corridas manuales del pipeline el mismo día.
ODDS_API_CACHE_TTL_SECONDS = int(os.getenv("ODDS_API_CACHE_TTL_SECONDS", "900"))  # 15 min
ODDS_API_MONTHLY_BUDGET = int(os.getenv("ODDS_API_MONTHLY_BUDGET", "500"))
ODDS_CACHE_DIR = os.getenv("ODDS_CACHE_DIR", ".cache/odds")


def resolve_odds_api_keys() -> list[str]:
    """Lista de API keys de The Odds API a rotar, en orden. Lee la variable
    de entorno ODDS_API_KEYS (nombres separados por coma) si está
    configurada; si no, cae a una lista de un solo elemento con
    ODDS_API_KEY (compatibilidad con quien ya tenía una sola key
    configurada -- nunca se rompe por agregar esta función). [] si
    ninguna de las dos variables está configurada.

    Es una función (no una constante congelada a nivel de módulo) para que
    data/odds_api.py la pueda llamar en cada fetch y así reflejar cambios
    de entorno en caliente (mismo criterio que ya usaba ODDS_API_KEY antes
    de este cambio, y que los tests dependen de poder monkeypatchear vía
    variables de entorno).

    Para activar una segunda key en producción: agrega el secret
    ODDS_API_KEYS="key1,key2" en GitHub (Settings -> Secrets and
    variables -> Actions) y pásalo como env: en
    .github/workflows/daily_pipeline.yml (ya viene configurado ahí junto a
    ODDS_API_KEY -- basta con crear el secret)."""
    keys_csv = os.getenv("ODDS_API_KEYS")
    if keys_csv:
        keys = [k.strip() for k in keys_csv.split(",") if k.strip()]
        if keys:
            return keys
    single = os.getenv("ODDS_API_KEY")
    return [single] if single else []


# Congelada al importar el módulo -- útil para quien solo quiera inspeccionar
# cuántas keys hay configuradas al arrancar (ej. un log de startup). El
# fetch real (data/odds_api.py) llama a resolve_odds_api_keys() de nuevo en
# cada corrida, no usa esta constante directamente.
ODDS_API_KEYS = resolve_odds_api_keys()

# Umbral de edge (en probabilidad) a partir del cual un juego se marca como
# "candidato a revisión" en el reporte — solo si además los dos VOTOS
# reales (heurístico vs. familia mu Skellam+NB2, que comparten el mismo
# mu proyectado y casi nunca discrepan entre sí) coinciden en el favorito.
# Es una preselección para que decidas tú, nunca una apuesta automática.
#
# Ojo: este umbral se compara contra GameAnalysis.away_edge/home_edge
# (heurístico vs. la MEJOR cuota disponible, CON vig) -- NO contra
# Pick.edge (la fuente configurada en PICK_PROBABILITY_SOURCE vs. el
# consenso SIN vig, ver model/picks.py). Son dos cálculos de "edge"
# distintos que conviven en el mismo reporte; flag_review solo mira el
# primero.
REVIEW_EDGE_THRESHOLD = float(os.getenv("REVIEW_EDGE_THRESHOLD", "0.03"))

# --- Señal de confianza alta (lista corta de máxima efectividad esperada) ---
# Un juego se marca high_confidence cuando la confianza del HEURÍSTICO en su
# favorito (max(prob, 1-prob)) alcanza este umbral. Por qué el heurístico y
# no Skellam/NegBin: el análisis de selectividad sobre las 4 temporadas
# históricas (2022-2025, 8,852 juegos con resultado real, 2026-07-12) mostró
# que el heurístico es el ÚNICO modelo cuya confianza alta es creíble --
# con confianza >= 0.575 acierta 66.7% (n=735, estable 64-70% en las 4
# temporadas), mientras que Skellam declarando >= 0.70 acierta apenas 59.5%
# (n=824, sobreconfianza estructural). Umbrales más exigentes que 0.575
# cruzan 70% de acierto solo con muestras minúsculas e inestables entre
# temporadas (n=76 en 4 años) -- no son un punto de operación confiable.
# Es una preselección informativa para el reporte, nunca una apuesta
# automática (mismo espíritu que flag_review).
HIGH_CONFIDENCE_THRESHOLD = float(os.getenv("HIGH_CONFIDENCE_THRESHOLD", "0.575"))

# --- Picks recomendados (moneyline / run_line / totals) ---
# Un candidato es "viable" si su EV o su edge superan estos umbrales
# (criterio OR, no AND). Si ningún mercado es viable y FORCE_AT_LEAST_ONE_PICK
# está activo, se genera igual el menos malo, marcado forced=True — nunca se
# mezcla con los picks reales en las métricas de desempeño.
MIN_PICK_EV = float(os.getenv("MIN_PICK_EV", "0.05"))
MIN_PICK_EDGE = float(os.getenv("MIN_PICK_EDGE", "0.04"))
FORCE_AT_LEAST_ONE_PICK = os.getenv("FORCE_AT_LEAST_ONE_PICK", "true").lower() == "true"
MAX_PICKS_PER_GAME = int(os.getenv("MAX_PICKS_PER_GAME", "3"))

# Con MIN_PICK_EV/MIN_PICK_EDGE tan bajos (5%/4%) sobre un modelo heurístico
# TODAVÍA SIN CALIBRAR (0 picks liquidados contra cuota real al momento de
# fijar estos umbrales), casi cualquier "edge" temprano es ruido/error del
# propio modelo, no ineficiencia real de mercado -- 200 es el orden de
# magnitud mínimo razonable para empezar a confiar en que un edge sostenido
# es señal y no varianza de muestra chica.
#
# Cuenta PICKS liquidados con cuota de mercado real (ver
# tracking.results_tracker.count_liquidated_picks_with_market_odds()), NO
# juegos con resultado final -- un juego sin cuota de mercado nunca puso a
# prueba ningún edge, así que no cuenta aquí aunque sí tenga resultado real
# (esa es la pregunta que ya responde print_calibration_report(), sobre la
# probabilidad cruda del modelo, no sobre el edge). Mientras el conteo esté
# por debajo de este número, los picks se marcan con calibration_phase=True
# (ver Pick.calibration_phase en db/database.py) -- se siguen generando y
# guardando igual, para poder acumular ese historial, pero no deberían
# tratarse como señal apostable todavía.
MIN_LIQUIDATED_PICKS_FOR_CALIBRATION = int(os.getenv("MIN_LIQUIDATED_PICKS_FOR_CALIBRATION", "200"))