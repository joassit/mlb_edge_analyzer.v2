"""Auditoria de poder discriminativo del Evidence Score -- seguimiento a
`historical/calibration.py`. El fit isotonico ya corrido contra las 5
temporadas reales mostro una calibracion excelente (`loso_ece~0.003`) pero
un poder discriminativo apenas por encima de un modelo sin skill
(`loso_brier~0.245` vs 0.25 de p=0.5 constante). Este modulo NO cambia
pillars/, engine/, calibration_registry ni el pipeline -- solo lee
`historical_report`/`historical_snapshot` ya ingeridos y responde POR QUE,
con evidencia real (siempre out-of-sample via leave-one-season-out, nunca
metricas de entrenamiento).

Toda comparacion de escenarios (ablacion de pilares, pesos alternativos,
shrinkage alternativo) reusa `calibration.py::loso_fit_and_score()` --
mismo criterio LOSO que valida la curva de produccion, nunca un split
distinto que pudiera inflar una mejora artificialmente.

Nota de alcance de la Fase 4 (optimizacion de pesos): el vector de pesos
candidato se aplica de forma ESTATICA e identica a todos los juegos (el
mismo rol que cumple `BASE_PILLAR_WEIGHTS`) -- no vuelve a correr el Rule
Engine/Weight Engine por juego (Seccion 6), que aplicaria deltas de
contexto por encima de esa base. Reconstruir eso exigiria re-evaluar el
Context Detector + Rule Engine para cada juego historico, fuera del
alcance de esta auditoria (que solo lee reportes ya persistidos).

Fuga de informacion en la Fase 4 -- por que existen DOS funciones:
`optimize_weights()` elige los pesos minimizando el LOSO log loss
agregado sobre TODAS las temporadas, y ese mismo LOSO es el que reporta
como "mejora" -- sesgo de seleccion (igual que reportar el score de un
k-fold CV usado para elegir hiperparametros como si fuera generalizacion).
`optimize_weights_nested()` es la version sin ese sesgo: nested LOSO --
por cada temporada externa, los pesos se optimizan usando SOLO las 4
restantes (su propio LOSO interno como objetivo) y se evaluan en la
externa con una curva ajustada UNICAMENTE sobre esas 4, nunca vista
durante esa busqueda de pesos. `optimize_weights()` sigue existiendo
porque hace falta un unico vector desplegable (ajustado con toda la
evidencia disponible); su propio numero de mejora esta marcado
explicitamente como optimista (`"warning"` en el resultado) -- la
pregunta "¿la mejora es real?" la responde `optimize_weights_nested()`."""

from __future__ import annotations

import logging
import resource
import subprocess
import time
from datetime import datetime, timezone

import numpy as np
from scipy.optimize import differential_evolution
from scipy.spatial.distance import jensenshannon
from scipy.stats import kurtosis, ks_2samp, pearsonr, skew
from sklearn.feature_selection import mutual_info_classif
from sklearn.metrics import auc, precision_recall_curve, roc_auc_score, roc_curve

from jsa.config import BASE_PILLAR_WEIGHTS, LEAGUE_AVG_ERA
from jsa.domain.models import SEVEN_PILLARS
from jsa.engine.pillars.base import discretize_diff, shrunk_era
from jsa.historical import calibration
from jsa.historical import db as historical_db
from jsa.historical.validation import _calibration_buckets

logger = logging.getLogger("jsa.historical")

_STARTER_UNIT_ERA_RUNS = 0.55
_BULLPEN_UNIT_ERA_RUNS = 0.45
_CLOSER_UNAVAILABLE_PENALTY = 0.30
_SHRINKAGE_K_CURRENT = 60.0


# --- Carga de datos (Seccion 8.1/7.1 -- ya persistidos, sin red) ---


def load_game_pillar_data(engine, seasons: list[int]) -> list[dict]:
    """Un registro por juego con resultado conocido: `evidence_score_raw`,
    el advantage discreto {-2..2} de cada pilar, el peso final aplicado
    (post Rule/Weight Engine) y los campos crudos de `GameSnapshot`
    (ERA/IP sin encoger) necesarios para la Fase 8 de shrinkage."""
    records: list[dict] = []
    for season in seasons:
        games = {g["game_pk"]: g for g in historical_db.games_for_season(engine, season)}
        snapshots = {s["game_pk"]: s for s in historical_db.snapshots_for_season(engine, season)}
        for report_row in historical_db.reports_for_season(engine, season):
            game = games.get(report_row["game_pk"])
            if game is None or game.get("winner") is None:
                continue
            payload = report_row["payload"]
            evidence_score_raw = payload.get("evidence_score_raw")
            pillar_advantages_raw = payload.get("pillar_advantages")
            final_weights = payload.get("final_weights")
            if evidence_score_raw is None or not pillar_advantages_raw or not final_weights:
                continue
            advantages = {pa["pillar"]: pa["advantage"] for pa in pillar_advantages_raw}
            if set(advantages) != set(SEVEN_PILLARS):
                continue
            snapshot_row = snapshots.get(report_row["game_pk"])
            records.append({
                "season": season,
                "game_pk": report_row["game_pk"],
                "home_win": 1 if game["winner"] == "home" else 0,
                "evidence_score_raw": float(evidence_score_raw),
                "advantages": advantages,
                "weights": {p: float(final_weights[p]) for p in SEVEN_PILLARS},
                "snapshot": (snapshot_row or {}).get("payload", {}),
            })
    return records


def _baseline_pairs_by_season(records: list[dict]) -> dict[int, list[tuple[float, int]]]:
    pairs_by_season: dict[int, list[tuple[float, int]]] = {}
    for r in records:
        pairs_by_season.setdefault(r["season"], []).append((r["evidence_score_raw"], r["home_win"]))
    return pairs_by_season


def _paired_bootstrap_ci(baseline_pairs: list[tuple[float, int]], alt_pairs: list[tuple[float, int]], n_resamples: int = 500, seed: int = 0) -> dict | None:
    """CI 90% del delta de Brier (alt - baseline) via bootstrap PAREADO
    sobre predicciones LOSO ya calculadas (nunca vuelve a entrenar nada,
    solo remuestrea que juegos entran en el promedio) -- para no aceptar
    un delta como real si el intervalo cruza cero."""
    n = len(baseline_pairs)
    if n == 0 or len(alt_pairs) != n:
        return None
    rng = np.random.default_rng(seed)
    base_sq_err = np.array([(p - y) ** 2 for p, y in baseline_pairs])
    alt_sq_err = np.array([(p - y) ** 2 for p, y in alt_pairs])
    diffs = alt_sq_err - base_sq_err
    resampled = np.array([diffs[rng.integers(0, n, n)].mean() for _ in range(n_resamples)])
    return {
        "delta_brier_mean": float(diffs.mean()),
        "ci_5": float(np.percentile(resampled, 5)),
        "ci_95": float(np.percentile(resampled, 95)),
        "significant": bool(np.percentile(resampled, 5) > 0 or np.percentile(resampled, 95) < 0),
    }


# --- Fase 1: auditoria individual de pilares ---


def _psi(dist_a: dict[int, int], dist_b: dict[int, int], support=(-2, -1, 0, 1, 2)) -> float:
    eps = 1e-6
    total_a = sum(dist_a.get(v, 0) for v in support) or 1
    total_b = sum(dist_b.get(v, 0) for v in support) or 1
    psi = 0.0
    for v in support:
        pct_a = max(dist_a.get(v, 0) / total_a, eps)
        pct_b = max(dist_b.get(v, 0) / total_b, eps)
        psi += (pct_b - pct_a) * np.log(pct_b / pct_a)
    return float(psi)


def _max_pairwise_psi(x: np.ndarray, seasons: np.ndarray) -> float:
    unique_seasons = sorted(set(seasons.tolist()))
    if len(unique_seasons) < 2:
        return 0.0
    dists = {}
    for s in unique_seasons:
        values, counts = np.unique(x[seasons == s], return_counts=True)
        dists[s] = {int(v): int(c) for v, c in zip(values, counts)}
    return max(
        _psi(dists[unique_seasons[i]], dists[unique_seasons[j]])
        for i in range(len(unique_seasons)) for j in range(i + 1, len(unique_seasons))
    )


def _score_with_pillar_override(records: list[dict], pillar: str, override_values: np.ndarray) -> list[tuple[float, int]]:
    scores = []
    for r, override in zip(records, override_values):
        s = sum(r["weights"][p] * (override if p == pillar else r["advantages"][p]) for p in SEVEN_PILLARS)
        scores.append(s)
    return scores


def _permutation_importance(pillar: str, records: list[dict], baseline_loso: dict, n_repeats: int = 3, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    original = np.array([r["advantages"][pillar] for r in records])
    seasons = np.array([r["season"] for r in records])
    brier_deltas = []
    for repeat in range(n_repeats):
        shuffled = original.copy()
        for s in np.unique(seasons):
            mask = seasons == s
            idx = np.where(mask)[0]
            shuffled_idx = rng.permutation(idx)
            shuffled[idx] = original[shuffled_idx]
        scores = _score_with_pillar_override(records, pillar, shuffled)
        pairs_by_season: dict[int, list[tuple[float, int]]] = {}
        for r, s in zip(records, scores):
            pairs_by_season.setdefault(r["season"], []).append((s, r["home_win"]))
        loso = calibration.loso_fit_and_score(pairs_by_season)
        if loso["loso_brier"] is not None and baseline_loso["loso_brier"] is not None:
            brier_deltas.append(loso["loso_brier"] - baseline_loso["loso_brier"])
    if not brier_deltas:
        return {"mean_brier_increase": None, "n_repeats": n_repeats}
    return {
        "mean_brier_increase": float(np.mean(brier_deltas)),
        "std_brier_increase": float(np.std(brier_deltas)),
        "n_repeats": n_repeats,
    }


def pillar_individual_stats(records: list[dict], baseline_loso: dict) -> dict[str, dict]:
    y = np.array([r["home_win"] for r in records])
    seasons = np.array([r["season"] for r in records])
    result: dict[str, dict] = {}
    for pillar in SEVEN_PILLARS:
        x = np.array([r["advantages"][pillar] for r in records], dtype=float)
        stats_out: dict = {"mean": float(x.mean()), "std": float(x.std())}
        values, counts = np.unique(x, return_counts=True)
        stats_out["distribution"] = {int(v): int(c) for v, c in zip(values, counts)}
        if len(values) > 1 and 0 < y.sum() < len(y):
            stats_out["auc"] = float(roc_auc_score(y, x))
            stats_out["ks_statistic"] = float(ks_2samp(x[y == 1], x[y == 0]).statistic)
            stats_out["ks_pvalue"] = float(ks_2samp(x[y == 1], x[y == 0]).pvalue)
            stats_out["mutual_information"] = float(mutual_info_classif(x.reshape(-1, 1), y, discrete_features=True, random_state=0)[0])
            stats_out["pearson_corr_with_outcome"] = float(pearsonr(x, y)[0])
        else:
            stats_out.update({"auc": None, "ks_statistic": None, "ks_pvalue": None, "mutual_information": None, "pearson_corr_with_outcome": None})
        stats_out["psi_max_between_seasons"] = _max_pairwise_psi(x, seasons)
        stats_out["permutation_importance"] = _permutation_importance(pillar, records, baseline_loso)
        result[pillar] = stats_out
    return result


# --- Fase 2: correlacion entre pilares ---


def pillar_correlation_matrices(records: list[dict]) -> dict:
    matrix = np.array([[r["advantages"][p] for p in SEVEN_PILLARS] for r in records], dtype=float)
    pearson = np.corrcoef(matrix, rowvar=False)
    n = len(SEVEN_PILLARS)
    spearman = np.eye(n)
    mi_matrix = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            xi, xj = matrix[:, i], matrix[:, j]
            if np.unique(xi).size > 1 and np.unique(xj).size > 1:
                from scipy.stats import spearmanr
                rho = float(spearmanr(xi, xj).statistic)
            else:
                rho = 0.0
            spearman[i, j] = spearman[j, i] = rho
            try:
                mi = float(mutual_info_classif(xi.reshape(-1, 1), xj.astype(int), discrete_features=True, random_state=0)[0])
            except Exception:
                mi = 0.0
            mi_matrix[i, j] = mi_matrix[j, i] = mi
    return {
        "pillars_order": list(SEVEN_PILLARS),
        "pearson": pearson.tolist(),
        "spearman": spearman.tolist(),
        "mutual_information": mi_matrix.tolist(),
    }


# --- Fase 3: ablacion (LOSO quitando un pilar a la vez) ---


def _score_excluding_pillar(records: list[dict], excluded: str) -> list[float]:
    kept = [p for p in SEVEN_PILLARS if p != excluded]
    scores = []
    for r in records:
        weight_sum = sum(r["weights"][p] for p in kept) or 1.0
        scores.append(sum((r["weights"][p] / weight_sum) * r["advantages"][p] for p in kept))
    return scores


def _classify_ablation_impact(ci: dict | None) -> str:
    if ci is None or ci["delta_brier_mean"] is None:
        return "no_demostrado"
    if not ci["significant"]:
        return "neutro"
    return "imprescindible" if ci["delta_brier_mean"] > 0 else "perjudicial"


def ablation_analysis(records: list[dict], baseline_loso: dict) -> dict:
    result = {
        "todos_los_pilares": {
            "loso_brier": baseline_loso["loso_brier"], "loso_log_loss": baseline_loso["loso_log_loss"],
            "loso_accuracy": baseline_loso["loso_accuracy"], "loso_ece": baseline_loso["loso_ece"],
        },
    }
    for pillar in SEVEN_PILLARS:
        scores = _score_excluding_pillar(records, pillar)
        pairs_by_season: dict[int, list[tuple[float, int]]] = {}
        for r, s in zip(records, scores):
            pairs_by_season.setdefault(r["season"], []).append((s, r["home_win"]))
        loso = calibration.loso_fit_and_score(pairs_by_season)
        ci = _paired_bootstrap_ci(baseline_loso["loso_pairs"], loso["loso_pairs"])
        result[f"sin_{pillar}"] = {
            "loso_brier": loso["loso_brier"], "loso_log_loss": loso["loso_log_loss"],
            "loso_accuracy": loso["loso_accuracy"], "loso_ece": loso["loso_ece"],
            "bootstrap_ci_delta_brier": ci,
            "classification": _classify_ablation_impact(ci),
        }
    return result


# --- Fase 4: optimizacion de BASE_PILLAR_WEIGHTS (LOSO log loss) ---


def _softmax(z: np.ndarray) -> np.ndarray:
    e = np.exp(z - z.max())
    return e / e.sum()


def _pairs_by_season_for_weights(advantages_matrix: np.ndarray, y: np.ndarray, seasons: np.ndarray, weights: np.ndarray) -> dict[int, list[tuple[float, int]]]:
    scores = advantages_matrix @ weights
    pairs_by_season: dict[int, list[tuple[float, int]]] = {}
    for s in np.unique(seasons):
        mask = seasons == s
        pairs_by_season[int(s)] = list(zip(scores[mask].tolist(), y[mask].tolist()))
    return pairs_by_season


def _optimize_weights_de(advantages_matrix: np.ndarray, y: np.ndarray, seasons: np.ndarray, *, seed: int, maxiter: int, popsize: int):
    """Un unico corrida de differential_evolution -- el objetivo (LOSO log
    loss) se computa SOLO sobre las temporadas presentes en `seasons`.
    Reusado tanto por el ajuste de produccion (todas las temporadas) como
    por cada fold externo de `optimize_weights_nested()` (solo las
    temporadas internas de ese fold)."""
    n = advantages_matrix.shape[1]

    def objective(z: np.ndarray) -> float:
        w = _softmax(z)
        pairs_by_season = _pairs_by_season_for_weights(advantages_matrix, y, seasons, w)
        loso = calibration.loso_fit_and_score(pairs_by_season)
        return loso["loso_log_loss"] if loso["loso_log_loss"] is not None else 10.0

    de_result = differential_evolution(
        objective, bounds=[(-3.0, 3.0)] * n, seed=seed, maxiter=maxiter, popsize=popsize,
        tol=1e-4, polish=True, workers=1,
    )
    return _softmax(de_result.x), de_result


def optimize_weights(records: list[dict], *, seed: int = 42, maxiter: int = 20, popsize: int = 10) -> dict:
    """Ajuste de PRODUCCION: un unico vector de pesos, optimizado sobre
    TODAS las temporadas disponibles (para tener un vector desplegable).

    Aviso de sesgo de seleccion: el `loso_log_loss`/`loso_brier` que este
    vector reporta AQUI es optimista -- el objetivo de
    `differential_evolution` es exactamente esa misma metrica agregada
    sobre las 5 temporadas, asi que no queda ninguna temporada realmente
    no vista para validar la eleccion (mismo problema que reportar el
    score de un k-fold CV usado para elegir hiperparametros como si fuera
    generalizacion). La estimacion SIN ese sesgo esta en
    `optimize_weights_nested()`, que es la que debe usarse para decidir si
    la mejora es real."""
    advantages_matrix = np.array([[r["advantages"][p] for p in SEVEN_PILLARS] for r in records], dtype=float)
    y = np.array([r["home_win"] for r in records])
    seasons = np.array([r["season"] for r in records])

    optimized_weights, de_result = _optimize_weights_de(advantages_matrix, y, seasons, seed=seed, maxiter=maxiter, popsize=popsize)
    current_weights = np.array([BASE_PILLAR_WEIGHTS[p] for p in SEVEN_PILLARS])

    current_pairs = _pairs_by_season_for_weights(advantages_matrix, y, seasons, current_weights)
    optimized_pairs = _pairs_by_season_for_weights(advantages_matrix, y, seasons, optimized_weights)
    current_loso = calibration.loso_fit_and_score(current_pairs)
    optimized_loso = calibration.loso_fit_and_score(optimized_pairs)
    ci = _paired_bootstrap_ci(current_loso["loso_pairs"], optimized_loso["loso_pairs"])

    def _pct(current, optimized):
        return float((current - optimized) / current * 100) if current else None

    return {
        "warning": "loso_brier/loso_log_loss de 'optimized_loso' tienen sesgo de seleccion -- son la MISMA metrica que differential_evolution uso como objetivo. Usar optimize_weights_nested() para la estimacion sin fuga de informacion.",
        "current_weights": dict(zip(SEVEN_PILLARS, current_weights.tolist())),
        "optimized_weights": dict(zip(SEVEN_PILLARS, optimized_weights.tolist())),
        "current_loso": {k: current_loso[k] for k in ("loso_brier", "loso_log_loss", "loso_accuracy", "loso_ece", "per_season_metrics")},
        "optimized_loso": {k: optimized_loso[k] for k in ("loso_brier", "loso_log_loss", "loso_accuracy", "loso_ece", "per_season_metrics")},
        "absolute_improvement_log_loss": current_loso["loso_log_loss"] - optimized_loso["loso_log_loss"],
        "pct_improvement_log_loss": _pct(current_loso["loso_log_loss"], optimized_loso["loso_log_loss"]),
        "absolute_improvement_brier": current_loso["loso_brier"] - optimized_loso["loso_brier"],
        "pct_improvement_brier": _pct(current_loso["loso_brier"], optimized_loso["loso_brier"]),
        "bootstrap_ci_delta_brier": ci,
        "optimizer_converged": bool(de_result.success),
        "optimizer_message": str(de_result.message),
        "optimizer_iterations": int(de_result.nit),
        "optimizer_n_function_evaluations": int(de_result.nfev),
    }


def optimize_weights_nested(records: list[dict], *, seed: int = 42, maxiter: int = 10, popsize: int = 6) -> dict:
    """Estimacion SIN sesgo de seleccion de si unos pesos optimizados
    generalizan: nested leave-one-season-out. Para cada temporada externa
    dejada afuera, los pesos se optimizan usando SOLO las 4 restantes (con
    su propio LOSO interno de 4 temporadas como objetivo de
    `differential_evolution` -- la temporada externa nunca entra en esa
    busqueda), y se evaluan sobre la temporada externa con una curva
    isotonica ajustada UNICAMENTE sobre las 4 internas. Ninguna
    prediccion reportada aqui proviene de un modelo (pesos + calibracion)
    que haya visto, directa o indirectamente, la temporada sobre la que se
    evalua -- esto es lo que permite afirmar que una mejora es real y no
    solo seleccion sobre el propio criterio de evaluacion."""
    seasons_list = sorted({r["season"] for r in records})
    current_weights = np.array([BASE_PILLAR_WEIGHTS[p] for p in SEVEN_PILLARS])

    outer_pairs_current: list[tuple[float, int]] = []
    outer_pairs_optimized: list[tuple[float, int]] = []
    per_season_weights: dict[int, dict] = {}

    for held_out in seasons_list:
        inner = [r for r in records if r["season"] != held_out]
        outer = [r for r in records if r["season"] == held_out]
        if not inner or not outer:
            continue

        inner_adv = np.array([[r["advantages"][p] for p in SEVEN_PILLARS] for r in inner], dtype=float)
        inner_y = np.array([r["home_win"] for r in inner])
        inner_seasons = np.array([r["season"] for r in inner])
        outer_adv = np.array([[r["advantages"][p] for p in SEVEN_PILLARS] for r in outer], dtype=float)
        outer_y = [r["home_win"] for r in outer]

        fold_started = time.perf_counter()
        w_opt, de_result = _optimize_weights_de(inner_adv, inner_y, inner_seasons, seed=seed, maxiter=maxiter, popsize=popsize)
        per_season_weights[held_out] = {
            "n_games_held_out": len(outer),
            "optimized_weights": dict(zip(SEVEN_PILLARS, w_opt.tolist())),
            "optimizer_n_function_evaluations": int(de_result.nfev),
            "optimizer_converged": bool(de_result.success),
            "fold_seconds": time.perf_counter() - fold_started,
        }

        for weights, sink in ((w_opt, outer_pairs_optimized), (current_weights, outer_pairs_current)):
            inner_scores = (inner_adv @ weights).tolist()
            model = calibration._fit_isotonic(list(zip(inner_scores, inner_y.tolist())))
            outer_scores = (outer_adv @ weights).tolist()
            preds = model.predict(outer_scores)
            sink.extend(zip((float(p) for p in preds), outer_y))

    from jsa.historical.validation import accuracy, brier_score, ece, log_loss

    current_metrics = {
        "brier": brier_score(outer_pairs_current), "log_loss": log_loss(outer_pairs_current),
        "accuracy": accuracy(outer_pairs_current), "ece": ece(outer_pairs_current),
    }
    optimized_metrics = {
        "brier": brier_score(outer_pairs_optimized), "log_loss": log_loss(outer_pairs_optimized),
        "accuracy": accuracy(outer_pairs_optimized), "ece": ece(outer_pairs_optimized),
    }
    ci = _paired_bootstrap_ci(outer_pairs_current, outer_pairs_optimized)

    return {
        "method": "nested_loso -- sin sesgo de seleccion: cada fold externo optimiza con las temporadas restantes y evalua en la temporada nunca vista por esa optimizacion",
        "n_outer_folds": len(per_season_weights),
        "per_season_optimized_weights": per_season_weights,
        "current_metrics_nested": current_metrics,
        "optimized_metrics_nested": optimized_metrics,
        "bootstrap_ci_delta_brier": ci,
        "generalizes": bool(ci and ci["significant"] and ci["delta_brier_mean"] < 0),
    }


# --- Fase 5: distribucion del Evidence Score ---


def score_distribution(records: list[dict]) -> dict:
    scores = np.array([r["evidence_score_raw"] for r in records])
    percentiles = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    counts, edges = np.histogram(scores, bins=20)
    return {
        "n": int(len(scores)),
        "mean": float(scores.mean()),
        "variance": float(scores.var()),
        "std": float(scores.std()),
        "skewness": float(skew(scores)),
        "kurtosis": float(kurtosis(scores)),
        "min": float(scores.min()),
        "max": float(scores.max()),
        "percentiles": {str(p): float(np.percentile(scores, p)) for p in percentiles},
        "histogram_counts": counts.tolist(),
        "histogram_bin_edges": edges.tolist(),
        "pct_within_plus_minus_0_5": float(np.mean(np.abs(scores) <= 0.5)),
    }


# --- Fase 6: separabilidad (ganados vs perdidos) ---


def separability_analysis(records: list[dict]) -> dict:
    scores = np.array([r["evidence_score_raw"] for r in records])
    y = np.array([r["home_win"] for r in records])
    won, lost = scores[y == 1], scores[y == 0]
    ks = ks_2samp(won, lost)
    pooled_var = ((len(won) - 1) * won.var(ddof=1) + (len(lost) - 1) * lost.var(ddof=1)) / (len(won) + len(lost) - 2)
    pooled_std = float(np.sqrt(pooled_var)) if pooled_var > 0 else 0.0
    cohens_d = float((won.mean() - lost.mean()) / pooled_std) if pooled_std > 0 else 0.0

    bins = np.linspace(scores.min(), scores.max(), 31)
    p_hist, _ = np.histogram(won, bins=bins)
    q_hist, _ = np.histogram(lost, bins=bins)
    p_dist = (p_hist + 1e-9) / (p_hist.sum() + 1e-9 * len(p_hist))
    q_dist = (q_hist + 1e-9) / (q_hist.sum() + 1e-9 * len(q_hist))

    return {
        "n_won": int(len(won)), "n_lost": int(len(lost)),
        "mean_won": float(won.mean()), "mean_lost": float(lost.mean()),
        "std_won": float(won.std()), "std_lost": float(lost.std()),
        "ks_statistic": float(ks.statistic), "ks_pvalue": float(ks.pvalue),
        "cohens_d": cohens_d,
        "jensen_shannon_divergence": float(jensenshannon(p_dist, q_dist) ** 2),
        "overlap_coefficient": float(np.sum(np.minimum(p_dist, q_dist))),
    }


# --- Fase 7: curvas de desempeno (solo predicciones LOSO out-of-sample) ---


def performance_curves(loso_pairs: list[tuple[float, int]]) -> dict:
    preds = np.array([p for p, _ in loso_pairs])
    actuals = np.array([y for _, y in loso_pairs])

    fpr, tpr, _ = roc_curve(actuals, preds)
    roc_auc_value = float(auc(fpr, tpr))
    fixed_fpr = np.linspace(0, 1, 21)
    tpr_interp = np.interp(fixed_fpr, fpr, tpr)

    precision, recall, _ = precision_recall_curve(actuals, preds)
    order = np.argsort(recall)
    fixed_recall = np.linspace(0, 1, 21)
    precision_interp = np.interp(fixed_recall, recall[order], precision[order])

    order_desc = np.argsort(-preds)
    sorted_actuals = actuals[order_desc]
    n = len(sorted_actuals)
    decile_size = max(1, n // 10)
    overall_rate = actuals.mean()
    total_positives = actuals.sum()
    lift_points, cum_gain_points = [], []
    cum_positives = 0.0
    for i in range(10):
        start, end = i * decile_size, (i + 1) * decile_size if i < 9 else n
        bucket = sorted_actuals[start:end]
        bucket_rate = bucket.mean() if len(bucket) else 0.0
        lift_points.append(float(bucket_rate / overall_rate) if overall_rate > 0 else 0.0)
        cum_positives += bucket.sum()
        cum_gain_points.append(float(cum_positives / total_positives) if total_positives > 0 else 0.0)

    reliability_buckets = _calibration_buckets(loso_pairs, n_bins=15)
    reliability = [
        {
            "bin_lo": b["lo"], "bin_hi": b["hi"], "n": len(b["preds"]),
            "mean_predicted": (sum(b["preds"]) / len(b["preds"])) if b["preds"] else None,
            "observed_frequency": (sum(b["actuals"]) / len(b["actuals"])) if b["actuals"] else None,
        }
        for b in reliability_buckets
    ]

    return {
        "roc_auc": roc_auc_value,
        "roc_curve": {"fpr": fixed_fpr.tolist(), "tpr": tpr_interp.tolist()},
        "precision_recall_curve": {"recall": fixed_recall.tolist(), "precision": precision_interp.tolist()},
        "lift_by_decile": lift_points,
        "cumulative_gain_by_decile": cum_gain_points,
        "reliability_diagram": reliability,
    }


# --- Fase 8: sensibilidad de shrinkage (starter + bullpen) ---


def _recompute_starter_bullpen_advantage(snapshot: dict, k_ip: float, original_advantages: dict) -> tuple[int, int]:
    league_era = snapshot.get("league_avg_era") or LEAGUE_AVG_ERA

    home_xera, away_xera = snapshot.get("home_starter_xera"), snapshot.get("away_starter_xera")
    if home_xera is None and away_xera is None:
        starter_adv = original_advantages["starter"]
    else:
        home_era = shrunk_era(home_xera, snapshot.get("home_starter_ip_sample") or 0, league_era, k_ip=k_ip) if home_xera is not None else league_era
        away_era = shrunk_era(away_xera, snapshot.get("away_starter_ip_sample") or 0, league_era, k_ip=k_ip) if away_xera is not None else league_era
        starter_adv = discretize_diff(away_era - home_era, _STARTER_UNIT_ERA_RUNS)

    home_bp, away_bp = snapshot.get("home_bullpen_era"), snapshot.get("away_bullpen_era")
    if home_bp is None and away_bp is None:
        bullpen_adv = original_advantages["bullpen"]
    else:
        home_e = shrunk_era(home_bp, snapshot.get("home_bullpen_ip_sample") or 0, league_era, k_ip=k_ip) if home_bp is not None else league_era
        away_e = shrunk_era(away_bp, snapshot.get("away_bullpen_ip_sample") or 0, league_era, k_ip=k_ip) if away_bp is not None else league_era
        home_eff = home_e + (_CLOSER_UNAVAILABLE_PENALTY if snapshot.get("home_closer_available") is False else 0.0)
        away_eff = away_e + (_CLOSER_UNAVAILABLE_PENALTY if snapshot.get("away_closer_available") is False else 0.0)
        bullpen_adv = discretize_diff(away_eff - home_eff, _BULLPEN_UNIT_ERA_RUNS)

    return starter_adv, bullpen_adv


def shrinkage_sensitivity(records: list[dict], baseline_loso: dict, k_values: tuple[float, ...] = (0.0, 20.0, _SHRINKAGE_K_CURRENT)) -> dict:
    results = {}
    for k in k_values:
        pairs_by_season: dict[int, list[tuple[float, int]]] = {}
        for r in records:
            starter_adv, bullpen_adv = _recompute_starter_bullpen_advantage(r["snapshot"], k, r["advantages"])
            score = sum(
                r["weights"][p] * (starter_adv if p == "starter" else bullpen_adv if p == "bullpen" else r["advantages"][p])
                for p in SEVEN_PILLARS
            )
            pairs_by_season.setdefault(r["season"], []).append((score, r["home_win"]))
        loso = calibration.loso_fit_and_score(pairs_by_season)
        ci = _paired_bootstrap_ci(baseline_loso["loso_pairs"], loso["loso_pairs"]) if k != _SHRINKAGE_K_CURRENT else None
        results[f"k_ip={k:g}"] = {
            "loso_brier": loso["loso_brier"], "loso_log_loss": loso["loso_log_loss"],
            "loso_accuracy": loso["loso_accuracy"], "loso_ece": loso["loso_ece"],
            "bootstrap_ci_delta_brier_vs_actual": ci,
        }
    return results


# --- Orquestador ---


def _current_commit_sha() -> str | None:
    """`GITHUB_SHA` (seteado automaticamente por GitHub Actions) si esta
    disponible; si no, `git rev-parse HEAD` local -- para poder saber
    exactamente que version del codigo produjo un resultado ya persistido,
    sin depender de cruzar manualmente el run de Actions con el repo."""
    import os

    env_sha = os.environ.get("GITHUB_SHA")
    if env_sha:
        return env_sha
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def _peak_rss_kb() -> int:
    """Maximo de RSS (KB en Linux) usado por el proceso HASTA este punto --
    `ru_maxrss` es un high-water-mark acumulado, nunca baja ni se resetea
    por fase; reportado despues de cada fase muestra si esa fase hizo
    subir el techo de memoria, no su consumo aislado (`tracemalloc` daria
    eso, pero con mas overhead -- no vale la pena para una auditoria
    one-off de solo lectura)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def run_full_audit(
    seasons: list[int], historical_database_url: str, *,
    optimizer_seed: int = 42, optimizer_maxiter: int = 20, optimizer_popsize: int = 10,
    nested_optimizer_maxiter: int = 10, nested_optimizer_popsize: int = 6,
) -> dict:
    engine = historical_db.get_engine(historical_database_url)
    historical_db.init_historical_storage(engine)
    records = load_game_pillar_data(engine, seasons)
    if not records:
        logger.warning("run_full_audit: 0 juegos con datos completos (pillar_advantages + final_weights + resultado) para %s", seasons)
        return {"n_games": 0, "seasons_used": seasons, "error": "no_games_with_full_data"}

    baseline_loso = calibration.loso_fit_and_score(_baseline_pairs_by_season(records))

    phase_timings_seconds: dict[str, float] = {}
    phase_peak_rss_kb: dict[str, int] = {}

    def _run_phase(name: str, fn, *args, **kwargs):
        started = time.perf_counter()
        value = fn(*args, **kwargs)
        phase_timings_seconds[name] = time.perf_counter() - started
        phase_peak_rss_kb[name] = _peak_rss_kb()
        return value

    result = {
        "run_metadata": {
            "commit_sha": _current_commit_sha(),
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "config": {
                "seasons_requested": seasons,
                "optimizer_seed": optimizer_seed,
                "optimizer_maxiter": optimizer_maxiter, "optimizer_popsize": optimizer_popsize,
                "nested_optimizer_maxiter": nested_optimizer_maxiter, "nested_optimizer_popsize": nested_optimizer_popsize,
                "min_games_per_season": calibration.MIN_GAMES_PER_SEASON,
                "min_seasons_for_walk_forward": calibration.MIN_SEASONS_FOR_WALK_FORWARD,
                "base_pillar_weights": dict(BASE_PILLAR_WEIGHTS),
                "shrinkage_k_values_tested": [0.0, 20.0, _SHRINKAGE_K_CURRENT],
            },
        },
        "n_games": len(records),
        "seasons_used": seasons,
        "baseline": {
            "loso_brier": baseline_loso["loso_brier"], "loso_log_loss": baseline_loso["loso_log_loss"],
            "loso_accuracy": baseline_loso["loso_accuracy"], "loso_ece": baseline_loso["loso_ece"],
            "loso_mce": baseline_loso["loso_mce"], "per_season_metrics": baseline_loso["per_season_metrics"],
        },
        "phase1_pillar_stats": _run_phase("phase1_pillar_stats", pillar_individual_stats, records, baseline_loso),
        "phase2_correlations": _run_phase("phase2_correlations", pillar_correlation_matrices, records),
        "phase3_ablation": _run_phase("phase3_ablation", ablation_analysis, records, baseline_loso),
        "phase4_weight_optimization": _run_phase(
            "phase4_weight_optimization", optimize_weights, records, seed=optimizer_seed, maxiter=optimizer_maxiter, popsize=optimizer_popsize,
        ),
        "phase4_weight_optimization_nested": _run_phase(
            "phase4_weight_optimization_nested", optimize_weights_nested, records,
            seed=optimizer_seed, maxiter=nested_optimizer_maxiter, popsize=nested_optimizer_popsize,
        ),
        "phase5_distribution": _run_phase("phase5_distribution", score_distribution, records),
        "phase6_separability": _run_phase("phase6_separability", separability_analysis, records),
        "phase7_curves": _run_phase("phase7_curves", performance_curves, baseline_loso["loso_pairs"]),
        "phase8_shrinkage": _run_phase("phase8_shrinkage", shrinkage_sensitivity, records, baseline_loso),
    }
    result["phase_timings_seconds"] = phase_timings_seconds
    result["phase_peak_rss_kb"] = phase_peak_rss_kb
    logger.info(
        "run_full_audit completo -- n_games=%d baseline_brier=%s baseline_ece=%s nested_generalizes=%s total_seconds=%.1f",
        len(records), baseline_loso["loso_brier"], baseline_loso["loso_ece"],
        result["phase4_weight_optimization_nested"]["generalizes"], sum(phase_timings_seconds.values()),
    )
    return result
