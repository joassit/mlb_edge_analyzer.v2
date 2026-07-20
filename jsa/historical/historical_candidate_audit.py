"""Auditoria descriptiva + comparacion LOSO de 4 candidatos de historial
head-to-head para el pilar Historical Favorite Context (`advantage=0`
siempre en produccion hoy -- ver `engine/pillars/historical.py`).

100% offline: calculado a partir de `historical_game` YA ingerido (no
requiere ninguna re-ingesta ni golpear la API de MLB), point-in-time-safe
(dia-batched, mismo criterio anti-fuga que `resolution_audit.py::
compute_elo_and_pythagorean()`), pero SIN resetear entre temporadas -- el
historial de enfrentamientos entre dos equipos especificos persiste entre
temporadas (no hay razon real para "olvidar" un enfrentamiento del año
pasado, a diferencia de Elo que si se reinicia por diseno documentado).

Limitacion honesta: "historial" aca significa "desde 2022" (el horizonte
de datos que este proyecto tiene ingerido), no el historial real completo
de la rivalidad -- se documenta, no se oculta.

Mismo patron que `trend_candidate_audit.py`: NUNCA reemplaza produccion,
solo mide si sustituir Historical (hoy siempre 0) por cada candidato,
z-scoreado y con el mismo peso que tiene hoy en `BASE_PILLAR_WEIGHTS`,
mejora el LOSO Brier de forma significativa (bootstrap CI) antes de
justificar implementarlo en `historical.py`."""

from __future__ import annotations

import logging

import numpy as np
from scipy.stats import ks_2samp, pearsonr
from sklearn.feature_selection import mutual_info_classif
from sklearn.metrics import roc_auc_score

from jsa.domain.models import SEVEN_PILLARS
from jsa.historical import calibration
from jsa.historical import db as historical_db
from jsa.historical.discriminative_audit import load_game_pillar_data
from jsa.historical.significance import paired_bootstrap_ci as _paired_bootstrap_ci

logger = logging.getLogger("jsa.historical")

HISTORICAL_CANDIDATES = ("h2h_win_pct_all_time", "h2h_win_pct_last_5", "h2h_run_diff_avg", "h2h_recency_weighted")

_RECENCY_DECAY = 0.8
_LAST_N = 5


def _compute_candidates_from_history(oriented: list[tuple[int, int]]) -> dict[str, float | None]:
    """`oriented`: lista de (score_equipo_actualmente_local,
    score_equipo_actualmente_visitante) de cada enfrentamiento PREVIO
    entre estos dos equipos, ya orientada a la perspectiva del partido
    actual, en orden cronologico (el ultimo elemento es el mas reciente).
    `None` en todos los candidatos si nunca se enfrentaron antes dentro
    de la ventana de datos ingerida."""
    if not oriented:
        return {c: None for c in HISTORICAL_CANDIDATES}

    wins = [1.0 if home_s > away_s else 0.0 for home_s, away_s in oriented]
    diffs = [float(home_s - away_s) for home_s, away_s in oriented]

    win_pct_all = sum(wins) / len(wins)
    last_n_wins = wins[-_LAST_N:]
    win_pct_last5 = sum(last_n_wins) / len(last_n_wins)
    run_diff_avg = sum(diffs) / len(diffs)

    n = len(wins)
    weights = [_RECENCY_DECAY ** (n - 1 - j) for j in range(n)]  # mas reciente (ultimo) -> peso 1.0
    weighted_win_pct = sum(w * win for w, win in zip(weights, wins)) / sum(weights)

    return {
        "h2h_win_pct_all_time": 2.0 * win_pct_all - 1.0,
        "h2h_win_pct_last_5": 2.0 * win_pct_last5 - 1.0,
        "h2h_run_diff_avg": run_diff_avg,
        "h2h_recency_weighted": 2.0 * weighted_win_pct - 1.0,
    }


def compute_head_to_head_history(engine, seasons: list[int]) -> dict[tuple[int, int], dict]:
    """Un registro por juego (`season`, `game_pk`) con los 4 candidatos
    calculados SOLO a partir de enfrentamientos previos entre esos dos
    equipos especificos (nunca incluye el juego actual ni ninguno
    posterior). Procesa TODAS las temporadas pedidas juntas, ordenadas
    por fecha real (no reinicia el historial en cada temporada), por
    lotes de dia calendario -- misma disciplina anti-fuga que
    `compute_elo_and_pythagorean()`: dentro de un mismo `game_date`,
    TODOS los juegos primero leen el estado pre-dia (primer loop), recien
    despues se actualiza el historial con los resultados de ese dia
    (segundo loop)."""
    all_games = []
    for season in seasons:
        for g in historical_db.games_for_season(engine, season):
            if g.get("winner") is not None:
                all_games.append({**g, "season": season})

    by_date: dict = {}
    for g in all_games:
        by_date.setdefault(g["game_date"], []).append(g)

    result: dict[tuple[int, int], dict] = {}
    pair_history: dict[tuple[int, int], list[dict]] = {}

    for game_date in sorted(by_date):
        day_games = by_date[game_date]
        for g in day_games:
            home_id, away_id = g["home_team_id"], g["away_team_id"]
            pair_key = (min(home_id, away_id), max(home_id, away_id))
            history = pair_history.get(pair_key, [])
            if home_id == pair_key[0]:
                oriented = [(m["lo_score"], m["hi_score"]) for m in history]
            else:
                oriented = [(m["hi_score"], m["lo_score"]) for m in history]
            result[(g["season"], g["game_pk"])] = {
                "n_meetings": len(history),
                **_compute_candidates_from_history(oriented),
            }

        for g in day_games:
            home_id, away_id = g["home_team_id"], g["away_team_id"]
            pair_key = (min(home_id, away_id), max(home_id, away_id))
            lo_score = g["home_score"] if home_id == pair_key[0] else g["away_score"]
            hi_score = g["away_score"] if home_id == pair_key[0] else g["home_score"]
            pair_history.setdefault(pair_key, []).append({"date": game_date, "lo_score": lo_score, "hi_score": hi_score})

    return result


def load_records_with_h2h_candidates(engine, seasons: list[int]) -> list[dict]:
    records = load_game_pillar_data(engine, seasons)
    h2h = compute_head_to_head_history(engine, seasons)
    out = []
    for r in records:
        entry = h2h.get((r["season"], r["game_pk"]))
        if entry is None:
            continue  # juego sin historical_game valido en el calculo de h2h (no deberia pasar, pero no se fuerza)
        r["n_meetings"] = entry["n_meetings"]
        r["h2h_candidate_diffs"] = {c: entry[c] for c in HISTORICAL_CANDIDATES}
        out.append(r)
    return out


def run_descriptive_audit(records: list[dict]) -> dict:
    """Cobertura (fraccion de juegos con >=1 enfrentamiento previo dentro
    de la ventana de datos), distribucion de `n_meetings` (cuantos
    enfrentamientos previos tenia en promedio cada juego), y distribucion
    + correlacion cruzada de los 4 candidatos (solo entre juegos con
    cobertura, calculando null en los que no aplica)."""
    n_games = len(records)
    n_meetings_arr = np.array([r["n_meetings"] for r in records], dtype=float)
    coverage_pct = float((n_meetings_arr > 0).mean()) if n_games else 0.0

    per_candidate = {}
    for c in HISTORICAL_CANDIDATES:
        raw = [r["h2h_candidate_diffs"][c] for r in records]
        non_null = [v for v in raw if v is not None]
        arr = np.array(non_null, dtype=float) if non_null else np.array([])
        per_candidate[c] = {
            "n_games": n_games,
            "n_non_null": len(non_null),
            "coverage_pct": (len(non_null) / n_games) if n_games else 0.0,
            "mean": float(arr.mean()) if arr.size else None,
            "std": float(arr.std()) if arr.size else None,
            "min": float(arr.min()) if arr.size else None,
            "p25": float(np.percentile(arr, 25)) if arr.size else None,
            "median": float(np.percentile(arr, 50)) if arr.size else None,
            "p75": float(np.percentile(arr, 75)) if arr.size else None,
            "max": float(arr.max()) if arr.size else None,
        }

    cross_candidate_correlations = {}
    for i, c1 in enumerate(HISTORICAL_CANDIDATES):
        for c2 in HISTORICAL_CANDIDATES[i + 1:]:
            paired = [
                (r["h2h_candidate_diffs"][c1], r["h2h_candidate_diffs"][c2]) for r in records
                if r["h2h_candidate_diffs"][c1] is not None and r["h2h_candidate_diffs"][c2] is not None
            ]
            if len(paired) <= 2:
                continue
            a = np.array([p[0] for p in paired], dtype=float)
            b = np.array([p[1] for p in paired], dtype=float)
            if a.std() > 0 and b.std() > 0:
                cross_candidate_correlations[f"{c1}__vs__{c2}"] = float(pearsonr(a, b)[0])

    return {
        "n_games_with_prior_meeting_coverage_pct": coverage_pct,
        "n_meetings_distribution": {
            "mean": float(n_meetings_arr.mean()) if n_games else None,
            "median": float(np.percentile(n_meetings_arr, 50)) if n_games else None,
            "p75": float(np.percentile(n_meetings_arr, 75)) if n_games else None,
            "max": float(n_meetings_arr.max()) if n_games else None,
        },
        "per_candidate": per_candidate,
        "cross_candidate_correlations": cross_candidate_correlations,
    }


def evaluate_historical_candidates(records: list[dict]) -> dict:
    """Mismo patron que `trend_candidate_audit.py::evaluate_trend_candidates()`:
    sustituye UNICAMENTE el valor de `historical` (z-scoreado sobre los
    juegos donde el candidato SI tiene dato, neutral/0 donde no -- juegos
    sin enfrentamiento previo en la ventana de datos) por el candidato,
    con el mismo peso que Historical tiene hoy. Comparado via bootstrap CI
    contra dejar Historical en 0 (estado real de produccion)."""
    y_all = np.array([r["home_win"] for r in records])
    baseline_pairs_by_season: dict[int, list[tuple[float, int]]] = {}
    for r in records:
        baseline_pairs_by_season.setdefault(r["season"], []).append((r["evidence_score_raw"], r["home_win"]))
    baseline_loso = calibration.loso_fit_and_score(baseline_pairs_by_season)

    out = {}
    for candidate in HISTORICAL_CANDIDATES:
        diffs = [r["h2h_candidate_diffs"][candidate] for r in records]
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

        z = np.array([
            ((d - mean) / std) if (d is not None and std > 0) else 0.0
            for d in diffs
        ])
        pairs_by_season: dict[int, list[tuple[float, int]]] = {}
        for r, zi in zip(records, z):
            score = sum(r["weights"][p] * (zi if p == "historical" else r["advantages"][p]) for p in SEVEN_PILLARS)
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
            "current_historical_per_season_metrics": baseline_loso["per_season_metrics"],
            "bootstrap_ci_delta_brier_vs_actual_historical": ci,
        }
    return out


def run_full_historical_candidate_audit(seasons: list[int], historical_database_url: str) -> dict:
    engine = historical_db.get_engine(historical_database_url)
    historical_db.init_historical_storage(engine)
    records = load_records_with_h2h_candidates(engine, seasons)
    if not records:
        return {"n_games": 0, "seasons_used": seasons, "error": "no_games_with_full_data"}

    result = {
        "n_games": len(records),
        "seasons_used": seasons,
        "phase_descriptive": run_descriptive_audit(records),
        "phase_loso_comparison": evaluate_historical_candidates(records),
    }
    logger.info("run_full_historical_candidate_audit completo -- n_games=%d", len(records))
    return result
