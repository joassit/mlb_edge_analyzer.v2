"""Backfill de `experiment_registry` con las 5 lineas de investigacion YA
cerradas con evidencia real (Trend, Historical, Statcast, Elo/Pythagorean,
Game Flow) -- Fase 3 (Seccion 12.8) exige que el Experiment Registry este
poblado con experimentos reales, no vacio (`experiment_registry` existia
desde la primera entrega pero sin ninguna fila, ver `registries/db.py`).
Estas 5 decisiones YA se tomaron con el protocolo LOSO + bootstrap CI +
criterio de tamano de efecto minimo (documentadas con evidencia real en
`jsa/docs/ROADMAP.md`) -- este modulo las formaliza como filas reales de
`experiment_registry` en vez de dejarlas solo en la documentacion. Nunca
recalcula nada: los numeros de abajo son los YA obtenidos en corridas
reales contra Postgres, citados con su `run_id` de GitHub Actions donde
aplica.

Idempotente (mismo patron que `registries/seed.py`): si un
`experiment_id` ya tiene fila, se salta -- correr esto dos veces no
duplica nada."""

from __future__ import annotations

from jsa import config
from jsa.registries import db

_SEASONS = [2022, 2023, 2024, 2025, 2026]

CLOSED_EXPERIMENTS: list[dict] = [
    {
        "experiment_id": "exp-team_quality-elo-v1",
        "description": (
            "Elo dinamico (elo_k=20, reinicio por temporada) como reemplazo de team_quality. "
            "Evaluado bajo el criterio formal de 3 condiciones (2026-07-19) sobre el dataset "
            "final post-reingesta de Trend (13,101 juegos, 2022-2026)."
        ),
        "feature_set": ["elo_diff"],
        "metrics_requested": ["brier", "auc"],
        "baseline_comparison": ["team_quality (produccion actual)"],
        "benchmarking_result": {
            "target_pillar": "team_quality", "auc": 0.559,
            "delta_brier_mean": 0.000460, "ci_90": [0.0000397, 0.000867],
            "significant": True, "effect_size_ok": False,
            "source": "jsa/docs/ROADMAP.md -- 'team_quality: Elo y Pythagorean Expectation bajo el criterio formal de 3 condiciones'",
        },
        "decision": "rejected",
    },
    {
        "experiment_id": "exp-team_quality-pythagorean-v1",
        "description": (
            "Pythagorean Expectation (exponente 1.83) como reemplazo de team_quality. "
            "Mismo dataset/corrida formal que Elo."
        ),
        "feature_set": ["pythagorean_diff"],
        "metrics_requested": ["brier", "auc"],
        "baseline_comparison": ["team_quality (produccion actual)"],
        "benchmarking_result": {
            "target_pillar": "team_quality", "auc": 0.559,
            "delta_brier_mean": 0.000479, "ci_90": [0.0000130, 0.000914],
            "significant": True, "effect_size_ok": False,
            "source": "jsa/docs/ROADMAP.md -- 'team_quality: Elo y Pythagorean Expectation bajo el criterio formal de 3 condiciones'",
        },
        "decision": "rejected",
    },
    {
        "experiment_id": "exp-statcast-h1-offense-xwoba-v1",
        "description": "xwOBA ofensivo de equipo (acumulado en temporada) como sustituto del pilar offense.",
        "feature_set": ["h1_offense_xwoba"],
        "metrics_requested": ["brier", "auc", "coverage"],
        "baseline_comparison": ["offense (produccion actual)"],
        "benchmarking_result": {
            "target_pillar": "offense", "auc": 0.517, "coverage_pct": 84.3,
            "delta_brier_mean": 0.001240, "significant": True, "effect_size_ok": True,
            "run_id": 29664006135, "source": "jsa/docs/ROADMAP.md -- 'Statcast Etapa 2'",
        },
        "decision": "rejected",
    },
    {
        "experiment_id": "exp-statcast-h2-starter-xwoba-allowed-v1",
        "description": "xwOBA permitido acumulado del abridor de ese juego especifico como sustituto del pilar starter.",
        "feature_set": ["h2_starter_xwoba_allowed"],
        "metrics_requested": ["brier", "auc", "coverage"],
        "baseline_comparison": ["starter (produccion actual)"],
        "benchmarking_result": {
            "target_pillar": "starter", "auc": 0.501, "coverage_pct": 59.4,
            "delta_brier_mean": 0.001233, "significant": True, "effect_size_ok": True,
            "run_id": 29664006135, "source": "jsa/docs/ROADMAP.md -- 'Statcast Etapa 2'",
        },
        "decision": "rejected",
    },
    {
        "experiment_id": "exp-statcast-h3-bullpen-xwoba-allowed-v1",
        "description": "xwOBA permitido acumulado del bullpen de equipo como sustituto del pilar bullpen.",
        "feature_set": ["h3_bullpen_xwoba_allowed"],
        "metrics_requested": ["brier", "auc", "coverage"],
        "baseline_comparison": ["bullpen (produccion actual)"],
        "benchmarking_result": {
            "target_pillar": "bullpen", "auc": 0.501, "coverage_pct": 84.2,
            "delta_brier_mean": 0.001523, "significant": True, "effect_size_ok": True,
            "run_id": 29664006135, "source": "jsa/docs/ROADMAP.md -- 'Statcast Etapa 2'",
        },
        "decision": "rejected",
    },
    {
        "experiment_id": "exp-statcast-h4-hard-hit-rolling-v1",
        "description": "Hard-hit rate de equipo, rolling 7d/14d, como candidato para el pilar trend.",
        "feature_set": ["h4_hard_hit_rolling_7d", "h4_hard_hit_rolling_14d"],
        "metrics_requested": ["brier", "auc", "coverage"],
        "baseline_comparison": ["trend (produccion actual, advantage=0 siempre)"],
        "benchmarking_result": {
            "target_pillar": "trend", "auc_7d": 0.515, "auc_14d": 0.510,
            "coverage_pct_7d": 35.5, "coverage_pct_14d": 54.5,
            "delta_brier_mean_7d": 0.000066, "delta_brier_mean_14d": 0.000001,
            "significant": False, "effect_size_ok": False,
            "run_id": 29664006135, "source": "jsa/docs/ROADMAP.md -- 'Statcast Etapa 2'",
        },
        "decision": "rejected",
    },
    {
        "experiment_id": "exp-trend-rolling-ops-era-v1",
        "description": "4 candidatos de forma reciente para el pilar trend: OPS/ERA de equipo, rolling 7d/14d.",
        "feature_set": ["ops_rolling_7d", "ops_rolling_14d", "era_rolling_7d", "era_rolling_14d"],
        "metrics_requested": ["brier", "auc", "coverage"],
        "baseline_comparison": ["trend (produccion actual, advantage=0 siempre)"],
        "benchmarking_result": {
            "target_pillar": "trend",
            "candidates": {
                "ops_rolling_7d": {"auc": 0.533, "coverage_pct": 85.1, "delta_brier_mean": -0.000006, "significant": False},
                "ops_rolling_14d": {"auc": 0.539, "coverage_pct": 85.5, "delta_brier_mean": 0.000104, "significant": False},
                "era_rolling_7d": {"auc": 0.529, "coverage_pct": 85.1, "delta_brier_mean": 0.000183, "significant": False},
                "era_rolling_14d": {"auc": 0.542, "coverage_pct": 85.5, "delta_brier_mean": 0.000340, "significant": True},
            },
            "run_id": 29621086180, "source": "jsa/docs/ROADMAP.md -- 'Resultado real de jsa_historical_trend_candidate_audit.yml'",
        },
        "decision": "rejected",
    },
    {
        "experiment_id": "exp-historical-head-to-head-v1",
        "description": (
            "4 candidatos de historial head-to-head para el pilar historical: win% all-time, "
            "win% ultimos 5, diferencia de carreras promedio, ponderado por recencia."
        ),
        "feature_set": ["h2h_win_pct_all_time", "h2h_win_pct_last_5", "h2h_run_diff_avg", "h2h_recency_weighted"],
        "metrics_requested": ["brier", "auc", "coverage"],
        "baseline_comparison": ["historical (produccion actual, advantage=0 siempre)"],
        "benchmarking_result": {
            "target_pillar": "historical",
            "candidates": {
                "h2h_win_pct_all_time": {"auc": 0.532, "coverage_pct": 96.1, "delta_brier_mean": 0.000051, "significant": False},
                "h2h_win_pct_last_5": {"auc": 0.524, "coverage_pct": 96.1, "delta_brier_mean": 0.000348, "significant": True},
                "h2h_run_diff_avg": {"auc": 0.539, "coverage_pct": 96.1, "delta_brier_mean": 0.000033, "significant": False},
                "h2h_recency_weighted": {"auc": 0.525, "coverage_pct": 96.1, "delta_brier_mean": 0.000136, "significant": False},
            },
            "run_id": 29625728340, "source": "jsa/docs/ROADMAP.md -- 'Resultado real de jsa_historical_historical_candidate_audit.yml'",
        },
        "decision": "rejected",
    },
    {
        "experiment_id": "exp-game_flow-gf1-starter-durability-v1",
        "description": "GF1: probabilidad de completar >=6 entradas (derivada de projected_ip) como sustituto del pilar starter.",
        "feature_set": ["gf1_starter_durability"],
        "metrics_requested": ["brier", "auc", "coverage"],
        "baseline_comparison": ["starter (produccion actual)"],
        "benchmarking_result": {
            "target_pillar": "starter", "auc": 0.522, "coverage_pct": 83.5,
            "delta_brier_mean": 0.000911, "significant": True, "effect_size_ok": False,
            "run_id": 29669963835, "source": "jsa/docs/ROADMAP.md -- 'Resultado real de jsa_game_flow_candidate_audit.yml'",
        },
        "decision": "rejected",
    },
    {
        "experiment_id": "exp-game_flow-gf2-bullpen-dependency-v1",
        "description": "GF2: ventaja de bullpen escalada por dependencia esperada (9 - projected_ip) como sustituto del pilar bullpen.",
        "feature_set": ["gf2_bullpen_dependency"],
        "metrics_requested": ["brier", "auc", "coverage"],
        "baseline_comparison": ["bullpen (produccion actual)"],
        "benchmarking_result": {
            "target_pillar": "bullpen", "auc": 0.561, "coverage_pct": 86.1,
            "delta_brier_mean": 0.000391, "significant": True, "effect_size_ok": False,
            "run_id": 29669963835, "source": "jsa/docs/ROADMAP.md -- 'Resultado real de jsa_game_flow_candidate_audit.yml'",
        },
        "decision": "rejected",
    },
]

for _exp in CLOSED_EXPERIMENTS:
    _exp.setdefault("rules", [])
    _exp.setdefault("date_range", {"seasons": _SEASONS})


def backfill_closed_experiments(engine) -> list[str]:
    """Inserta las filas de `CLOSED_EXPERIMENTS` que todavia no existan
    (idempotente por `experiment_id`, mismo criterio que
    `registries/seed.py`). Devuelve los `experiment_id` REALMENTE
    insertados en esta llamada (vacio si ya estaban todos)."""
    db.init_registries(engine)
    existing = db.latest_by_id(engine, db.experiment_registry, "experiment_id")
    inserted: list[str] = []
    for exp in CLOSED_EXPERIMENTS:
        if exp["experiment_id"] in existing:
            continue
        db.append(
            engine, db.experiment_registry,
            experiment_id=exp["experiment_id"], description=exp["description"],
            base_model_version=config.MODEL_VERSION, feature_set=exp["feature_set"], weights={},
            rules=exp["rules"], date_range=exp["date_range"], metrics_requested=exp["metrics_requested"],
            baseline_comparison=exp["baseline_comparison"], benchmarking_result=exp["benchmarking_result"],
            decision=exp["decision"],
        )
        inserted.append(exp["experiment_id"])
    return inserted
