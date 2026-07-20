"""Configuracion central de JSA v3.0.

Disciplina heredada de `mlb_edge_analyzer.v2/config.py`: todo valor
"plausible pero sin validar contra un experimento real" queda etiquetado
como tal y neutralizado (no amplificado) hasta que exista evidencia. Nada
de esto se hardcodea dentro del engine -- Principio 12 (ningun cambio de
parametro es silencioso, todo vive en config versionada en git).
"""

from __future__ import annotations

import os

# --- Temporada / base de datos ---
# `os.getenv(key) or default` en vez de `os.getenv(key, default)`: la
# segunda forma solo aplica el default cuando la variable esta AUSENTE, no
# cuando existe pero esta VACIA -- y un workflow de GitHub Actions que
# asigna `env: X: ${{ secrets.X }}` sin el secret configurado exporta
# justamente eso: una variable presente con valor "". Le tumbo una corrida
# real a `jsa_historical_ingest.yml` (season 2022, run 29260616665,
# "Could not parse SQLAlchemy URL from given URL string") antes de que
# este patron se corrigiera aqui.
SEASON = int(os.getenv("JSA_SEASON") or "2026")

# Nombre de secret/env distinto al DATABASE_URL de mlb_edge_analyzer.v2 a
# proposito -- son dos proyectos hermanos con historiales independientes;
# compartir el nombre de variable arriesgaria que alguien apunte por error
# ambos pipelines a la misma base de datos y mezcle sus tablas.
DATABASE_URL = os.getenv("JSA_DATABASE_URL") or "sqlite:///jsa.db"

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

MODEL_VERSION = "0.1.0-experimental"

# --- Pesos base de los 7 pilares (Seccion 6.1 del spec) ---
BASE_PILLAR_WEIGHTS: dict[str, float] = {
    "starter": 0.22,
    "bullpen": 0.25,
    "offense": 0.20,
    "team_quality": 0.15,
    "context": 0.08,
    "trend": 0.05,
    "historical": 0.05,
}

# --- Shrinkage / ajustes estadisticos ---
# Mismos puntos de partida que mlb_edge_analyzer.v2 (literatura sabermetrica
# estandar), NO calibrados todavia contra historial propio de JSA -- este
# proyecto arranca sin datos acumulados. Se recalibran en cuanto exista
# suficiente historial en el Feature Store (mismo criterio que
# MIN_LIQUIDATED_PICKS_FOR_CALIBRATION en el proyecto viejo).
LEAGUE_AVG_ERA = 4.30
LEAGUE_AVG_RUNS_PER_GAME = 4.50
FALLBACK_BULLPEN_ERA = 4.30
SHRINKAGE_K_IP = 60.0
OFFENSE_FACTOR_EXPONENT = 1.8
MIN_PA_FOR_LEAGUE_OPS = 100

# --- Modulo de Carreras Proyectadas (Seccion 9) ---
STARTER_WEIGHT_IN_PITCHING = 0.65
HOME_FIELD_RUNS_BONUS = 0.15

# --- Umbrales del Context Detector (Seccion 5, valores exactos del spec) ---
LONG_OUTING_IP = 6.5
SHORT_OUTING_IP = 5.0
BULLPEN_FATIGUE_IP_3D = 10.0
KEY_INJURIES_THRESHOLD = 2
EXTREME_TRAVEL_MILES = 2000
EXTREME_WEATHER_COLD_F = 45
EXTREME_WEATHER_HOT_F = 95
EXTREME_WEATHER_WIND_MPH = 20
SMALL_SAMPLE_OFFENSE_PA = 50

# --- CRI (Seccion 8.2) ---
CRI_COMPONENTS: dict[str, int] = {
    "starters_confirmed": 18,
    "lineups_official": 18,
    "bullpen_usage_known": 12,
    "no_last_minute_changes": 12,
    "xera_available": 8,
    "xfip_available": 7,
    "missing_projected_ip": -10,
}

# --- Uncertainty Index (Seccion 8.3) ---
UNCERTAINTY_BASE = 40
UNCERTAINTY_BULLPEN_FATIGUE = 15
UNCERTAINTY_EXTREME_WEATHER = 12
UNCERTAINTY_DOUBLE_HEADER = 8
UNCERTAINTY_EXTREME_TRAVEL = 10
UNCERTAINTY_EXTREME_TRAVEL_MILES = 2500
UNCERTAINTY_PER_INJURY = 4
UNCERTAINTY_INJURY_CAP = 20
UNCERTAINTY_DEGRADE_CATEGORY_THRESHOLD = 80

# --- Final Category (Seccion 8.4) -- solo aplicable sobre score CALIBRADO.
# Con el sistema sin calibrar (ver engine/decision_engine.py), estos
# umbrales existen pero decision_engine.py fuerza
# final_category="NO_DISPONIBLE_SIN_CALIBRAR" en vez de usarlos.
EVIDENCE_THRESHOLD_CLEAR_FAVORITE = 1.6
EVIDENCE_THRESHOLD_MODERATE_FAVORITE = 0.8
CRI_THRESHOLD_CLEAR_FAVORITE = 70

# --- Consistency Flag (Seccion 9.3) ---
CONSISTENCY_CRI_PENALTY = 10

# --- Confidence Gate (Seccion 10.2) -- valores de partida, sin Gate
# Threshold Sweep (10.3) todavia. El Gate nunca pasa mientras el modelo
# este sin calibrar (ver engine/confidence_gate.py), sin importar estos
# numeros -- se dejan aqui porque el mecanismo debe existir desde el dia 1.
GATE_P_MIN = 0.65
GATE_CRI_MIN = 85
GATE_UNCERTAINTY_MAX = 40
GATE_DOMINANCE_THRESHOLD = 0.40

# --- Calibracion (Seccion 8.4.1/9.2, Fase 4) -- el UNICO calibration_id
# que engine/orchestrator.py puede leer de calibration_registry para
# decidir calibration_status. Mismo valor que el default de
# `historical/cli.py calibrate --calibration-id` -- si se ajusta una
# curva nueva bajo otro id, nunca se usa en produccion hasta que se
# actualice esta constante a mano (nunca automatico).
PRODUCTION_CALIBRATION_ID = "calibration-evidence_score_raw-v1"

# Mercados base (Seccion 10.5) -- sembrados como `active` en el Market
# Registry desde el dia 1 (son el set fijo de 10.5, no una extension de
# 10.5bis). Un mercado nuevo se agrega via Market Registry, nunca editando
# esta lista in situ.
MARKET_IDS: tuple[str, ...] = ("moneyline_home", "moneyline_away", "run_line", "totals")

# --- Odds API (mismo patron probado de proteccion de presupuesto) ---
ODDS_API_CACHE_TTL_SECONDS = int(os.getenv("JSA_ODDS_API_CACHE_TTL_SECONDS") or "900")
ODDS_API_MONTHLY_BUDGET = int(os.getenv("JSA_ODDS_API_MONTHLY_BUDGET") or "500")
ODDS_CACHE_DIR = os.getenv("JSA_ODDS_CACHE_DIR") or "jsa/.cache/odds"


def resolve_odds_api_keys() -> list[str]:
    """Lista de API keys de The Odds API a rotar, en orden. Funcion (no
    constante congelada) para reflejar cambios de entorno en caliente y
    para que los tests puedan monkeypatchear variables de entorno."""
    keys_csv = os.getenv("JSA_ODDS_API_KEYS")
    if keys_csv:
        keys = [k.strip() for k in keys_csv.split(",") if k.strip()]
        if keys:
            return keys
    single = os.getenv("JSA_ODDS_API_KEY")
    return [single] if single else []


NETWORK_TIMEOUT_SECONDS = 15
WEATHER_TIMEOUT_SECONDS = 6
