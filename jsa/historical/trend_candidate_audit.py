"""Auditoria descriptiva + comparacion LOSO de los 4 candidatos de forma
reciente para el pilar Trend (rolling OPS/ERA a 7d/14d, recolectados desde
PR #25/schema 3.4, todavia NO wireados en `trend.py` -- que sigue
devolviendo `advantage=0` siempre).

Mismo patron que `resolution_audit.py::evaluate_team_quality_alternatives()`:
NUNCA reemplaza produccion. Mide si sustituir Trend (hoy siempre 0) por
cada candidato, z-scoreado y con el MISMO peso que Trend tiene hoy en
`BASE_PILLAR_WEIGHTS`, mejora el LOSO Brier de forma estadisticamente
significativa (bootstrap CI) -- solo si algun candidato pasa ese umbral
se justifica implementarlo de verdad en `trend.py` (ver ROADMAP.md)."""

from __future__ import annotations

import logging

import numpy as np
from scipy.stats import ks_2samp, pearsonr
from sklearn.feature_selection import mutual_info_classif
from sklearn.metrics import roc_auc_score

from jsa.domain.models import SEVEN_PILLARS
from jsa.engine.pillars.base import offense_factor
from jsa.historical import calibration
from jsa.historical import db as historical_db
from jsa.historical.discriminative_audit import load_game_pillar_data
from jsa.historical.significance import paired_bootstrap_ci as _paired_bootstrap_ci

logger = logging.getLogger("jsa.historical")

_LEAGUE_OPS_FALLBACK = 0.750

TREND_CANDIDATES = ("ops_rolling_7d", "ops_rolling_14d", "era_rolling_7d", "era_rolling_14d")

ROLLING_FIELD_NAMES = (
    "home_team_ops_rolling_7d", "away_team_ops_rolling_7d",
    "home_team_ops_rolling_14d", "away_team_ops_rolling_14d",
    "home_team_era_rolling_7d", "away_team_era_rolling_7d",
    "home_team_era_rolling_14d", "away_team_era_rolling_14d",
)


def _ops_rolling_diff(snapshot: dict, window: str) -> float | None:
    home = snapshot.get(f"home_team_ops_rolling_{window}")
    away = snapshot.get(f"away_team_ops_rolling_{window}")
    if home is None or away is None:
        return None
    league_ops = snapshot.get("league_avg_ops") or _LEAGUE_OPS_FALLBACK
    return offense_factor(home, league_ops) - offense_factor(away, league_ops)


def _era_rolling_diff(snapshot: dict, window: str) -> float | None:
    home = snapshot.get(f"home_team_era_rolling_{window}")
    away = snapshot.get(f"away_team_era_rolling_{window}")
    if home is None or away is None:
        return None
    return away - home  # menor ERA = mejor forma -> away-home mantiene positivo=ventaja home


_DIFF_FNS = {
    "ops_rolling_7d": lambda s: _ops_rolling_diff(s, "7d"),
    "ops_rolling_14d": lambda s: _ops_rolling_diff(s, "14d"),
    "era_rolling_7d": lambda s: _era_rolling_diff(s, "7d"),
    "era_rolling_14d": lambda s: _era_rolling_diff(s, "14d"),
}


def load_records_with_trend_candidates(engine, seasons: list[int]) -> list[dict]:
    """Extiende `load_game_pillar_data()` con el diff continuo (home-away,
    positivo=ventaja home) de cada uno de los 4 candidatos -- `None` si el
    juego no tenia ventana rolling completa todavia (inicio de temporada)."""
    records = load_game_pillar_data(engine, seasons)
    for r in records:
        snapshot = r["snapshot"]
        r["trend_candidate_diffs"] = {c: _DIFF_FNS[c](snapshot) for c in TREND_CANDIDATES}
    return records


def run_descriptive_audit(records: list[dict]) -> dict:
    """Fase descriptiva pura: cobertura, distribucion (mean/std/percentiles/
    extremos) de cada uno de los 8 campos crudos, y correlacion cruzada
    entre ellos (ej. cuanto se solapan OPS 7d vs 14d)."""
    per_field = {}
    for field in ROLLING_FIELD_NAMES:
        raw = [r["snapshot"].get(field) for r in records]
        non_null = [v for v in raw if v is not None]
        arr = np.array(non_null, dtype=float) if non_null else np.array([])
        per_field[field] = {
            "n_games": len(records),
            "n_non_null": len(non_null),
            "coverage_pct": (len(non_null) / len(records)) if records else 0.0,
            "mean": float(arr.mean()) if arr.size else None,
            "std": float(arr.std()) if arr.size else None,
            "min": float(arr.min()) if arr.size else None,
            "p25": float(np.percentile(arr, 25)) if arr.size else None,
            "median": float(np.percentile(arr, 50)) if arr.size else None,
            "p75": float(np.percentile(arr, 75)) if arr.size else None,
            "max": float(arr.max()) if arr.size else None,
        }

    cross_field_correlations = {}
    for i, f1 in enumerate(ROLLING_FIELD_NAMES):
        for f2 in ROLLING_FIELD_NAMES[i + 1:]:
            paired = [
                (r["snapshot"].get(f1), r["snapshot"].get(f2)) for r in records
                if r["snapshot"].get(f1) is not None and r["snapshot"].get(f2) is not None
            ]
            if len(paired) <= 2:
                continue
            a = np.array([p[0] for p in paired], dtype=float)
            b = np.array([p[1] for p in paired], dtype=float)
            if a.std() > 0 and b.std() > 0:
                cross_field_correlations[f"{f1}__vs__{f2}"] = float(pearsonr(a, b)[0])

    return {"per_field": per_field, "cross_field_correlations": cross_field_correlations}


def evaluate_trend_candidates(records: list[dict]) -> dict:
    """Fase LOSO: para cada candidato, sustituye UNICAMENTE el valor de
    `trend` (z-scoreado sobre los juegos donde el candidato SI tiene dato,
    neutral/0 donde no) por el candidato, con el mismo peso que Trend tiene
    hoy. Comparado via bootstrap CI contra dejar Trend en 0 (estado real de
    produccion) -- `significant=True` con delta_brier negativo es la unica
    evidencia que justificaria implementar el candidato en `trend.py`."""
    y_all = np.array([r["home_win"] for r in records])
    baseline_pairs_by_season: dict[int, list[tuple[float, int]]] = {}
    for r in records:
        baseline_pairs_by_season.setdefault(r["season"], []).append((r["evidence_score_raw"], r["home_win"]))
    baseline_loso = calibration.loso_fit_and_score(baseline_pairs_by_season)

    out = {}
    for candidate in TREND_CANDIDATES:
        diffs = [r["trend_candidate_diffs"][candidate] for r in records]
        non_null_mask = np.array([d is not None for d in diffs])
        non_null_diffs = np.array([d for d in diffs if d is not None], dtype=float)
        n_missing = int((~non_null_mask).sum())

        if non_null_diffs.size > 1 and non_null_diffs.std() > 0:
            y_non_null = y_all[non_null_mask]
            individual_stats = {
                "n_missing": n_missing,
                "coverage_pct": float(non_null_mask.mean()),
                "auc": float(roc_auc_score(y_non_null, non_null_diffs)) if np.unique(non_null_diffs).size > 1 else None,
                "ks_statistic": float(ks_2samp(non_null_diffs[y_non_null == 1], non_null_diffs[y_non_null == 0]).statistic),
                "pearson_corr_with_outcome": float(pearsonr(non_null_diffs, y_non_null)[0]),
                "mutual_information": float(mutual_info_classif(non_null_diffs.reshape(-1, 1), y_non_null, random_state=0)[0]),
            }
            mean, std = float(non_null_diffs.mean()), float(non_null_diffs.std())
        else:
            individual_stats = {
                "n_missing": n_missing, "coverage_pct": float(non_null_mask.mean()),
                "auc": None, "ks_statistic": None, "pearson_corr_with_outcome": None, "mutual_information": None,
            }
            mean, std = 0.0, 0.0

        # z-score sobre la distribucion REAL (solo juegos con dato) -- los
        # juegos sin ventana completa quedan en z=0 (neutral, igual que el
        # advantage=0 que Trend produce hoy para TODOS los juegos).
        z = np.array([
            ((d - mean) / std) if (d is not None and std > 0) else 0.0
            for d in diffs
        ])
        pairs_by_season: dict[int, list[tuple[float, int]]] = {}
        for r, zi in zip(records, z):
            score = sum(r["weights"][p] * (zi if p == "trend" else r["advantages"][p]) for p in SEVEN_PILLARS)
            pairs_by_season.setdefault(r["season"], []).append((score, r["home_win"]))
        loso = calibration.loso_fit_and_score(pairs_by_season)
        ci = _paired_bootstrap_ci(baseline_loso["loso_pairs"], loso["loso_pairs"])
        out[candidate] = {
            "individual_stats": individual_stats,
            "loso_if_substituted": {
                "loso_brier": loso["loso_brier"], "loso_log_loss": loso["loso_log_loss"],
                "loso_accuracy": loso["loso_accuracy"], "loso_ece": loso["loso_ece"],
                "per_season_metrics": loso["per_season_metrics"],
            },
            "current_trend_per_season_metrics": baseline_loso["per_season_metrics"],
            "bootstrap_ci_delta_brier_vs_actual_trend": ci,
        }
    return out


def run_full_trend_candidate_audit(seasons: list[int], historical_database_url: str) -> dict:
    engine = historical_db.get_engine(historical_database_url)
    historical_db.init_historical_storage(engine)
    records = load_records_with_trend_candidates(engine, seasons)
    if not records:
        return {"n_games": 0, "seasons_used": seasons, "error": "no_games_with_full_data"}

    result = {
        "n_games": len(records),
        "seasons_used": seasons,
        "phase_descriptive": run_descriptive_audit(records),
        "phase_loso_comparison": evaluate_trend_candidates(records),
    }
    logger.info("run_full_trend_candidate_audit completo -- n_games=%d", len(records))
    return result
