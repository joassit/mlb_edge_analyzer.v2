"""
Auditoría científica del modelo sobre las 4 temporadas históricas
(2022-2025) -- corre DENTRO de GitHub Actions contra las historical_backtest.db
reales (artifacts historical-raw-logs-{season}). Usa sqlite3 directo (las 4
DBs en un proceso), las funciones puras de historical_engine/stats_utils.py
y model/predictor.py::predict_from_raw_inputs (la función REAL de
producción) para permutation importance, sensibilidad y ablación.

Fases cubiertas numéricamente: 1 (rendimiento por temporada), 2 (por motor),
3 (diferencia de carreras), 4 (importancia de variables), 5 (sensibilidad),
6 (sesgos por segmento), 7 (calibración con IC), 8 (drift), 11 (benchmarks
ingenuos), y la comparación 2024 pre-fix (run_id=1) vs. post-fix (run nuevo)
sobre la MISMA base -- pareada por game_pk.

Uso: python scratchpad_scientific_audit.py 2022=dbs/2022.db 2023=dbs/2023.db ...
Imprime un solo JSON entre ===JSON_SCI_AUDIT_START=== / ===JSON_SCI_AUDIT_END===.
"""

import json
import math
import random
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, ".")

from scipy.stats import kendalltau

from config import (
    STARTER_WEIGHT, HOME_FIELD_ADVANTAGE, PARK_FACTOR_WEIGHT,
    WEATHER_CORRECTION, NEGBIN_DISPERSION,
)
from model.predictor import predict_from_raw_inputs
from model.runs_projection import LEAGUE_AVG_ERA, LEAGUE_AVG_RUNS_PER_GAME
from historical_engine.stats_utils import (
    brier_score, log_loss, mae, rmse, bias, pearson_corr, spearman_corr,
    r_squared, calibration_buckets, expected_calibration_error,
    maximum_calibration_error, sharpness, bootstrap_ci,
)

DEFAULT_LEAGUE_OPS = 0.715

SOURCES = ("heuristic", "skellam", "negbin")

PROB_COLS = {
    "heuristic": ("away_model_prob", "home_model_prob"),
    "skellam": ("away_skellam_prob", "home_skellam_prob"),
    "negbin": ("away_negbin_prob", "home_negbin_prob"),
}


def wilson_ci(hits: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = hits / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (center - half, center + half)


def q(conn, sql, args=()):
    cur = conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def season_runs(conn):
    return q(conn, "SELECT id, run_type, season_year, model_version, git_commit, status, "
                   "n_games_processed, n_games_skipped, n_errors FROM historical_run "
                   "WHERE run_type='season' ORDER BY id")


def load_rows(conn, run_id):
    """Join analysis+game del run: una fila por juego con resultado real."""
    rows = q(conn, """
        SELECT a.game_pk, a.game_date, a.season_year,
               a.away_era, a.home_era, a.away_innings_pitched, a.home_innings_pitched,
               a.away_ops, a.home_ops, a.away_team_pa, a.home_team_pa,
               a.away_bullpen_era, a.home_bullpen_era,
               a.away_k_pct, a.home_k_pct, a.away_bb_pct, a.home_bb_pct,
               a.away_days_rest, a.home_days_rest,
               a.park_factor, a.park_name, a.temp_f,
               a.away_proj_runs, a.home_proj_runs,
               a.away_model_prob, a.home_model_prob,
               a.away_skellam_prob, a.home_skellam_prob,
               a.away_negbin_prob, a.home_negbin_prob,
               g.home_score, g.away_score, g.winner
        FROM historical_analysis a
        JOIN historical_game g ON g.game_pk = a.game_pk AND g.run_id = a.run_id
        WHERE a.run_id = ? AND g.winner IS NOT NULL
          AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL
    """, (run_id,))
    return rows


def raw_inputs(r, starter_weight=STARTER_WEIGHT):
    required = (r["away_era"], r["home_era"], r["away_ops"], r["home_ops"],
                r["away_bullpen_era"], r["home_bullpen_era"], r["park_factor"])
    if any(v is None for v in required):
        return None
    return {
        "away_era": r["away_era"], "home_era": r["home_era"],
        "away_ops": r["away_ops"], "home_ops": r["home_ops"],
        "away_bullpen_era": r["away_bullpen_era"], "home_bullpen_era": r["home_bullpen_era"],
        "away_innings_pitched": r["away_innings_pitched"], "home_innings_pitched": r["home_innings_pitched"],
        "away_k_pct": r["away_k_pct"], "home_k_pct": r["home_k_pct"],
        "away_bb_pct": r["away_bb_pct"], "home_bb_pct": r["home_bb_pct"],
        "away_days_rest": r["away_days_rest"], "home_days_rest": r["home_days_rest"],
        "park_factor": r["park_factor"], "temp_f": r["temp_f"],
        "starter_weight": starter_weight,
        "league_ops": DEFAULT_LEAGUE_OPS, "league_era": LEAGUE_AVG_ERA,
        "league_avg_runs_per_game": LEAGUE_AVG_RUNS_PER_GAME,
        "park_factor_weight": PARK_FACTOR_WEIGHT, "weather_correction": WEATHER_CORRECTION,
        "home_field_advantage": HOME_FIELD_ADVANTAGE, "negbin_dispersion": NEGBIN_DISPERSION,
        # alpha=1.0: reproduce el motor SIN calibrar, igual que las
        # probabilidades ya congeladas en la ingesta (la calibración 0.5 es
        # de producción 2026; la comparación interna baseline-vs-perturbado
        # debe compartir el mismo alpha para ser válida).
        "skellam_shrinkage_alpha": 1.0,
    }


# ---------------- FASE 1/2/7: métricas por motor ----------------

def model_metrics(rows, source):
    ac, hc = PROB_COLS[source]
    probs, outcomes, confidences, hits = [], [], [], []
    for r in rows:
        hp = r[hc]
        if hp is None:
            continue
        out = 1 if r["winner"] == "home" else 0
        probs.append(hp)
        outcomes.append(out)
        conf = max(hp, 1 - hp)
        pred_home = hp >= 0.5
        confidences.append(conf)
        hits.append(pred_home == (out == 1))
    n = len(probs)
    if n == 0:
        return {"n": 0}
    buckets = calibration_buckets(confidences, hits)
    for b in buckets:
        ci = wilson_ci(b["hits"], b["n"]) if b["n"] else None
        b["hit_rate_ci"] = ci
    acc = sum(hits) / n
    return {
        "n": n,
        "accuracy": acc,
        "accuracy_ci": wilson_ci(sum(hits), n),
        "brier_score": brier_score(probs, outcomes),
        "log_loss": log_loss(probs, outcomes),
        "ece": expected_calibration_error(buckets, n),
        "mce": maximum_calibration_error(buckets),
        "sharpness": sharpness(confidences),
        "buckets": buckets,
        "prob_hist": prob_histogram(probs),
        "mean_home_prob": sum(probs) / n,
    }


def prob_histogram(probs, bins=10):
    counts = [0] * bins
    for p in probs:
        idx = min(int(p * bins), bins - 1)
        counts[idx] += 1
    return counts


# ---------------- FASE 3: diferencia de carreras ----------------

def margin_analysis(rows, sample_seed=42, sample_n=400):
    ok = [r for r in rows if r["home_proj_runs"] is not None and r["away_proj_runs"] is not None]
    proj_margin = [r["home_proj_runs"] - r["away_proj_runs"] for r in ok]
    actual_margin = [r["home_score"] - r["away_score"] for r in ok]
    proj_total = [r["home_proj_runs"] + r["away_proj_runs"] for r in ok]
    actual_total = [r["home_score"] + r["away_score"] for r in ok]
    errors_m = [p - a for p, a in zip(proj_margin, actual_margin)]
    errors_t = [p - a for p, a in zip(proj_total, actual_total)]

    kt_m = kendalltau(proj_margin, actual_margin)
    rel_err_t = [abs(e) / max(a, 1) for e, a in zip(errors_t, actual_total)]

    # slices
    def _acc_slice(sel):
        sub = [r for r in ok if sel(r)]
        if len(sub) < 20:
            return {"n": len(sub)}
        h, tot = 0, 0
        briers = []
        for r in sub:
            hp = r["home_skellam_prob"]
            if hp is None:
                continue
            out = 1 if r["winner"] == "home" else 0
            h += 1 if ((hp >= 0.5) == (out == 1)) else 0
            tot += 1
            briers.append((hp - out) ** 2)
        return {"n": tot, "skellam_accuracy": h / tot if tot else None,
                "skellam_brier": sum(briers) / len(briers) if briers else None}

    slices = {
        "proj_close_lt1": _acc_slice(lambda r: abs(r["home_proj_runs"] - r["away_proj_runs"]) < 1.0),
        "proj_blowout_ge2": _acc_slice(lambda r: abs(r["home_proj_runs"] - r["away_proj_runs"]) >= 2.0),
        "fav_home": _acc_slice(lambda r: (r["home_skellam_prob"] or 0) > 0.5),
        "fav_away": _acc_slice(lambda r: (r["home_skellam_prob"] or 1) <= 0.5),
    }

    # distribución del margen: proyectado nunca alcanza colas reales?
    def _dist(xs):
        xs_s = sorted(xs)
        n = len(xs_s)
        def pct(p):
            return xs_s[min(int(p * n), n - 1)]
        return {"mean": sum(xs) / n, "std": (sum((x - sum(xs)/n) ** 2 for x in xs) / n) ** 0.5,
                "p5": pct(0.05), "p25": pct(0.25), "p50": pct(0.50), "p75": pct(0.75), "p95": pct(0.95)}

    rng = random.Random(sample_seed)
    idx = list(range(len(ok)))
    rng.shuffle(idx)
    sample_idx = sorted(idx[:sample_n])

    mean_e = sum(errors_m) / len(errors_m)
    std_e = (sum((e - mean_e) ** 2 for e in errors_m) / len(errors_m)) ** 0.5 or 1.0
    std_err_sorted = sorted((e - mean_e) / std_e for e in errors_m)
    nn = len(std_err_sorted)
    qq = [std_err_sorted[min(int(p / 100 * nn), nn - 1)] for p in range(1, 100)]

    def _hist(xs, lo, hi, bins=25):
        counts = [0] * bins
        for x in xs:
            if x < lo or x >= hi:
                continue
            counts[int((x - lo) / (hi - lo) * bins)] += 1
        return {"lo": lo, "hi": hi, "bins": bins, "counts": counts,
                "n_outside": sum(1 for x in xs if x < lo or x >= hi)}

    return {
        "n": len(ok),
        "margin": {
            "mae": mae(proj_margin, actual_margin), "rmse": rmse(proj_margin, actual_margin),
            "bias": bias(proj_margin, actual_margin),
            "pearson": pearson_corr(proj_margin, actual_margin),
            "spearman": spearman_corr(proj_margin, actual_margin),
            "kendall": float(kt_m.statistic), "kendall_p": float(kt_m.pvalue),
            "r2": r_squared(proj_margin, actual_margin),
            "proj_dist": _dist(proj_margin), "actual_dist": _dist(actual_margin),
        },
        "total": {
            "mae": mae(proj_total, actual_total), "rmse": rmse(proj_total, actual_total),
            "bias": bias(proj_total, actual_total),
            "pearson": pearson_corr(proj_total, actual_total),
            "spearman": spearman_corr(proj_total, actual_total),
            "r2": r_squared(proj_total, actual_total),
            "mean_abs_rel_error": sum(rel_err_t) / len(rel_err_t),
            "proj_dist": _dist(proj_total), "actual_dist": _dist(actual_total),
        },
        "slices": slices,
        "home_actual_margin_mean": sum(actual_margin) / len(actual_margin),
        "home_proj_margin_mean": sum(proj_margin) / len(proj_margin),
        "charts": {
            "scatter_margin": [[round(proj_margin[i], 2), actual_margin[i]] for i in sample_idx],
            "scatter_total": [[round(proj_total[i], 2), actual_total[i]] for i in sample_idx],
            "hist_error_margin": _hist(errors_m, -12, 12),
            "hist_error_total": _hist(errors_t, -14, 14),
            "qq_margin_error": [round(v, 3) for v in qq],
        },
    }


# ---------------- FASE 4: variables ----------------

VAR_GROUPS = {
    "era_abridor": ("away_era", "home_era"),
    "ops_equipo": ("away_ops", "home_ops"),
    "bullpen_era": ("away_bullpen_era", "home_bullpen_era"),
    "k_pct": ("away_k_pct", "home_k_pct"),
    "bb_pct": ("away_bb_pct", "home_bb_pct"),
    "descanso": ("away_days_rest", "home_days_rest"),
    "park_factor": ("park_factor",),
    "temp_f": ("temp_f",),
    "ip_muestra": ("away_innings_pitched", "home_innings_pitched"),
}


def variable_correlations(rows):
    """Correlación de cada diferencial (ventaja local) con ganar el juego y
    con el margen real, y matriz de colinealidad entre diferenciales."""
    feats = {
        "era_diff_ventaja_local": lambda r: (r["away_era"] - r["home_era"]) if None not in (r["away_era"], r["home_era"]) else None,
        "ops_diff_ventaja_local": lambda r: (r["home_ops"] - r["away_ops"]) if None not in (r["home_ops"], r["away_ops"]) else None,
        "bullpen_diff_ventaja_local": lambda r: (r["away_bullpen_era"] - r["home_bullpen_era"]) if None not in (r["away_bullpen_era"], r["home_bullpen_era"]) else None,
        "kpct_diff_ventaja_local": lambda r: (r["home_k_pct"] - r["away_k_pct"]) if None not in (r["home_k_pct"], r["away_k_pct"]) else None,
        "bbpct_diff_ventaja_local": lambda r: (r["away_bb_pct"] - r["home_bb_pct"]) if None not in (r["away_bb_pct"], r["home_bb_pct"]) else None,
        "rest_diff": lambda r: (r["home_days_rest"] - r["away_days_rest"]) if None not in (r["home_days_rest"], r["away_days_rest"]) else None,
        "park_factor": lambda r: r["park_factor"],
        "temp_f": lambda r: r["temp_f"],
    }
    out = {"vs_outcome": {}, "vs_margin": {}, "collinearity": {}, "coverage": {}}
    vals = {}
    for name, fn in feats.items():
        pairs = [(fn(r), 1 if r["winner"] == "home" else 0, r["home_score"] - r["away_score"]) for r in rows]
        pairs = [(v, o, m) for v, o, m in pairs if v is not None]
        out["coverage"][name] = len(pairs)
        if len(pairs) < 30:
            continue
        xs = [p[0] for p in pairs]
        out["vs_outcome"][name] = pearson_corr(xs, [p[1] for p in pairs])
        out["vs_margin"][name] = pearson_corr(xs, [float(p[2]) for p in pairs])
        vals[name] = {r["game_pk"]: fn(r) for r in rows if fn(r) is not None}
    names = list(vals.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            common = [pk for pk in vals[a] if pk in vals[b]]
            if len(common) < 30:
                continue
            c = pearson_corr([vals[a][pk] for pk in common], [vals[b][pk] for pk in common])
            if c is not None and abs(c) >= 0.15:
                out["collinearity"][f"{a}~{b}"] = c
    return out


def permutation_importance(rows, reps=3):
    """Baraja cada grupo de variables entre juegos (manteniendo el par
    away/home junto) y mide el aumento de Brier al recalcular con el motor
    real. Se calcula para Skellam y para el heurístico por separado --
    k_pct/bb_pct/descanso solo alimentan al heurístico por diseño."""
    base_inputs = []
    outcomes = []
    for r in rows:
        ri = raw_inputs(r)
        if ri is None:
            continue
        base_inputs.append(ri)
        outcomes.append(1 if r["winner"] == "home" else 0)
    n = len(base_inputs)
    base_sk, base_he = [], []
    for ri in base_inputs:
        pred = predict_from_raw_inputs(ri)
        base_sk.append(pred["home_skellam_prob"])
        base_he.append(pred["home_model_prob"])
    baseline = {"n": n,
                "skellam_brier": brier_score(base_sk, outcomes),
                "heuristic_brier": brier_score(base_he, outcomes)}

    importance = {}
    for group, cols in VAR_GROUPS.items():
        deltas_sk, deltas_he = [], []
        for rep in range(reps):
            rng = random.Random(1000 + rep)
            perm = list(range(n))
            rng.shuffle(perm)
            sk_probs, he_probs = [], []
            for i, ri in enumerate(base_inputs):
                mod = dict(ri)
                src = base_inputs[perm[i]]
                for c in cols:
                    mod[c] = src[c]
                pred = predict_from_raw_inputs(mod)
                sk_probs.append(pred["home_skellam_prob"])
                he_probs.append(pred["home_model_prob"])
            deltas_sk.append(brier_score(sk_probs, outcomes) - baseline["skellam_brier"])
            deltas_he.append(brier_score(he_probs, outcomes) - baseline["heuristic_brier"])
        importance[group] = {
            "skellam_delta_brier": sum(deltas_sk) / reps,
            "heuristic_delta_brier": sum(deltas_he) / reps,
        }
    return {"baseline": baseline, "importance": importance}


# ---------------- FASE 5: sensibilidad ----------------

SENS_VARS = ["home_era", "home_ops", "home_bullpen_era", "park_factor", "temp_f"]
SENS_LEVELS = [-0.20, -0.10, -0.05, 0.05, 0.10, 0.20]


def sensitivity(rows, sample_n=300, seed=7):
    rng = random.Random(seed)
    pool = [r for r in rows if raw_inputs(r) is not None]
    rng.shuffle(pool)
    pool = pool[:sample_n]
    base = []
    for r in pool:
        ri = raw_inputs(r)
        base.append((ri, predict_from_raw_inputs(ri)))
    out = {}
    for var in SENS_VARS:
        levels = {}
        for lv in SENS_LEVELS:
            dp, dr = [], []
            for ri, bp in base:
                if ri.get(var) is None:
                    continue
                mod = dict(ri)
                mod[var] = ri[var] * (1 + lv)
                pred = predict_from_raw_inputs(mod)
                dp.append(abs(pred["home_skellam_prob"] - bp["home_skellam_prob"]))
                dr.append(abs(pred["home_proj_runs"] - bp["home_proj_runs"]))
            if dp:
                levels[f"{lv:+.0%}"] = {
                    "mean_abs_delta_prob": sum(dp) / len(dp),
                    "max_abs_delta_prob": max(dp),
                    "mean_abs_delta_home_runs": sum(dr) / len(dr),
                    "n": len(dp),
                }
        out[var] = levels
    return out


# ---------------- FASE 6: sesgos por segmento ----------------

def _seg_metrics(rows, source="skellam"):
    ac, hc = PROB_COLS[source]
    probs, outcomes = [], []
    hits = 0
    for r in rows:
        hp = r[hc]
        if hp is None:
            continue
        out = 1 if r["winner"] == "home" else 0
        probs.append(hp)
        outcomes.append(out)
        hits += 1 if ((hp >= 0.5) == (out == 1)) else 0
    n = len(probs)
    if n < 30:
        return {"n": n}
    return {"n": n, "accuracy": hits / n, "brier": brier_score(probs, outcomes),
            "accuracy_ci": wilson_ci(hits, n)}


def bias_segments(rows):
    def month(r):
        return r["game_date"][5:7]
    segs = {
        "abridor_elite_alguno (ERA<=3.20)": lambda r: (r["away_era"] is not None and r["away_era"] <= 3.2) or (r["home_era"] is not None and r["home_era"] <= 3.2),
        "abridor_debil_alguno (ERA>=5.00)": lambda r: (r["away_era"] is not None and r["away_era"] >= 5.0) or (r["home_era"] is not None and r["home_era"] >= 5.0),
        "abridor_muestra_chica (IP<30 alguno)": lambda r: (r["away_innings_pitched"] is not None and r["away_innings_pitched"] < 30) or (r["home_innings_pitched"] is not None and r["home_innings_pitched"] < 30),
        "parque_ofensivo (pf>=1.05)": lambda r: r["park_factor"] is not None and r["park_factor"] >= 1.05,
        "parque_pitcher (pf<=0.95)": lambda r: r["park_factor"] is not None and r["park_factor"] <= 0.95,
        "clima_calor (temp>=85F)": lambda r: r["temp_f"] is not None and r["temp_f"] >= 85,
        "clima_frio (temp<60F)": lambda r: r["temp_f"] is not None and r["temp_f"] < 60,
        "inicio_temporada (abr-may)": lambda r: month(r) in ("03", "04", "05"),
        "mitad_temporada (jun-jul)": lambda r: month(r) in ("06", "07"),
        "final_temporada (ago-oct)": lambda r: month(r) in ("08", "09", "10"),
    }
    out = {name: _seg_metrics([r for r in rows if sel(r)]) for name, sel in segs.items()}
    # sesgo local/visitante del modelo vs. realidad
    n = len(rows)
    home_wins = sum(1 for r in rows if r["winner"] == "home")
    fav_home = sum(1 for r in rows if (r["home_skellam_prob"] or 0) > 0.5)
    mean_hp = sum(r["home_skellam_prob"] for r in rows if r["home_skellam_prob"] is not None) / n
    out["_base_rates"] = {
        "n": n, "home_win_rate_real": home_wins / n,
        "modelo_favorece_local_rate": fav_home / n,
        "prob_local_promedio_skellam": mean_hp,
    }
    return out


# ---------------- FASE 11: benchmarks ingenuos ----------------

def benchmarks(rows):
    n = len(rows)
    home_wins = sum(1 for r in rows if r["winner"] == "home")

    def rule_acc(pick_home_fn):
        hits = usable = 0
        for r in rows:
            ph = pick_home_fn(r)
            if ph is None:
                continue
            usable += 1
            hits += 1 if (ph == (r["winner"] == "home")) else 0
        return {"n": usable, "accuracy": hits / usable if usable else None}

    out = {
        "siempre_local": rule_acc(lambda r: True),
        "favorito_por_ERA_abridor": rule_acc(
            lambda r: (r["home_era"] < r["away_era"]) if None not in (r["home_era"], r["away_era"]) and r["home_era"] != r["away_era"] else None),
        "favorito_por_OPS": rule_acc(
            lambda r: (r["home_ops"] > r["away_ops"]) if None not in (r["home_ops"], r["away_ops"]) and r["home_ops"] != r["away_ops"] else None),
        "moneda_brier": 0.25,
        "constante_home_rate_brier": brier_score([home_wins / n] * n, [1 if r["winner"] == "home" else 0 for r in rows]),
        "home_win_rate": home_wins / n,
    }
    return out


# ---------------- 2024 antes/después (pareado) ----------------

def before_after(conn, run_old, run_new):
    out = {"run_old": run_old, "run_new": run_new}
    rows_old = {r["game_pk"]: r for r in load_rows(conn, run_old)}
    rows_new = {r["game_pk"]: r for r in load_rows(conn, run_new)}
    common = sorted(set(rows_old) & set(rows_new))
    out["n_old"], out["n_new"], out["n_common"] = len(rows_old), len(rows_new), len(common)
    out["only_old"], out["only_new"] = len(rows_old) - len(common), len(rows_new) - len(common)

    # métricas agregadas por motor en cada run (todas sus filas)
    out["metrics_old"] = {s: {k: v for k, v in model_metrics(list(rows_old.values()), s).items()
                              if k not in ("buckets", "prob_hist")} for s in SOURCES}
    out["metrics_new"] = {s: {k: v for k, v in model_metrics(list(rows_new.values()), s).items()
                              if k not in ("buckets", "prob_hist")} for s in SOURCES}

    # comparación PAREADA sobre el subconjunto común
    paired = {}
    for s in SOURCES:
        _, hc = PROB_COLS[s]
        both_ok = both_bad = old_ok_new_bad = old_bad_new_ok = 0
        briers_old, briers_new, dprobs = [], [], []
        for pk in common:
            ro, rn = rows_old[pk], rows_new[pk]
            if ro[hc] is None or rn[hc] is None:
                continue
            outv = 1 if rn["winner"] == "home" else 0
            co = (ro[hc] >= 0.5) == (outv == 1)
            cn = (rn[hc] >= 0.5) == (outv == 1)
            both_ok += co and cn
            both_bad += (not co) and (not cn)
            old_ok_new_bad += co and not cn
            old_bad_new_ok += (not co) and cn
            briers_old.append((ro[hc] - outv) ** 2)
            briers_new.append((rn[hc] - outv) ** 2)
            dprobs.append(abs(rn[hc] - ro[hc]))
        nn = len(briers_old)
        disc = old_ok_new_bad + old_bad_new_ok
        mcnemar_chi2 = ((abs(old_ok_new_bad - old_bad_new_ok) - 1) ** 2 / disc) if disc > 0 else None
        paired[s] = {
            "n_paired": nn,
            "brier_old": sum(briers_old) / nn if nn else None,
            "brier_new": sum(briers_new) / nn if nn else None,
            "mean_abs_prob_change": sum(dprobs) / nn if nn else None,
            "pct_prob_change_gt_2pp": sum(1 for d in dprobs if d > 0.02) / nn if nn else None,
            "flips": {"both_ok": both_ok, "both_bad": both_bad,
                      "old_ok_new_bad": old_ok_new_bad, "old_bad_new_ok": old_bad_new_ok},
            "mcnemar_chi2_cc": mcnemar_chi2,
        }
    out["paired"] = paired

    # cuánto cambió cada INSUMO entre corridas (efecto directo de los fixes)
    input_changes = {}
    for col in ("away_bullpen_era", "home_bullpen_era", "temp_f", "away_era", "home_era", "away_ops", "home_ops"):
        deltas = []
        for pk in common:
            vo, vn = rows_old[pk][col], rows_new[pk][col]
            if vo is None or vn is None:
                continue
            deltas.append(abs(vn - vo))
        if deltas:
            deltas_sorted = sorted(deltas)
            input_changes[col] = {
                "n": len(deltas),
                "mean_abs_delta": sum(deltas) / len(deltas),
                "p50": deltas_sorted[len(deltas) // 2],
                "p90": deltas_sorted[int(len(deltas) * 0.9)],
                "pct_changed_gt_0p10": sum(1 for d in deltas if d > 0.10) / len(deltas),
            }
    out["input_changes"] = input_changes

    # slice: decil de juegos con MAYOR cambio de bullpen -- ¿ahí mejoró el Brier?
    bp_delta = []
    for pk in common:
        ro, rn = rows_old[pk], rows_new[pk]
        if None in (ro["away_bullpen_era"], rn["away_bullpen_era"], ro["home_bullpen_era"], rn["home_bullpen_era"], rn["home_skellam_prob"], ro["home_skellam_prob"]):
            continue
        d = abs(rn["away_bullpen_era"] - ro["away_bullpen_era"]) + abs(rn["home_bullpen_era"] - ro["home_bullpen_era"])
        bp_delta.append((d, pk))
    bp_delta.sort(reverse=True)
    top = [pk for _, pk in bp_delta[:max(len(bp_delta) // 10, 20)]]
    bo, bn = [], []
    for pk in top:
        outv = 1 if rows_new[pk]["winner"] == "home" else 0
        bo.append((rows_old[pk]["home_skellam_prob"] - outv) ** 2)
        bn.append((rows_new[pk]["home_skellam_prob"] - outv) ** 2)
    out["top_decile_bullpen_change"] = {
        "n": len(top),
        "skellam_brier_old": sum(bo) / len(bo) if bo else None,
        "skellam_brier_new": sum(bn) / len(bn) if bn else None,
    }
    return out


# ---------------- simulaciones previas guardadas ----------------

def stored_simulations(conn):
    try:
        rows = q(conn, "SELECT season_year, param_name, baseline_value, proposed_value, "
                       "baseline_metric_value, proposed_metric_value, improved "
                       "FROM historical_simulation ORDER BY id")
        return rows
    except sqlite3.OperationalError:
        return []


# ---------------- main ----------------

def audit_season(db_path, season):
    conn = sqlite3.connect(db_path)
    try:
        runs = season_runs(conn)
        this_season = [r for r in runs if r["season_year"] == season]
        result = {"season": season, "runs": this_season}
        if not this_season:
            result["error"] = "sin corridas de temporada en esta base"
            return result
        run_id = max(r["id"] for r in this_season)
        rows = load_rows(conn, run_id)
        result["run_id_used"] = run_id
        result["n_games_with_result"] = len(rows)

        result["models"] = {s: model_metrics(rows, s) for s in SOURCES}
        result["margin"] = margin_analysis(rows)
        result["variables"] = variable_correlations(rows)
        result["permutation_importance"] = permutation_importance(rows)
        result["sensitivity"] = sensitivity(rows)
        result["bias_segments"] = bias_segments(rows)
        result["benchmarks"] = benchmarks(rows)
        result["variable_distributions"] = {
            col: _col_stats(rows, col) for col in
            ("away_era", "home_era", "away_ops", "home_ops", "away_bullpen_era",
             "home_bullpen_era", "park_factor", "temp_f")
        }
        result["stored_simulations"] = stored_simulations(conn)

        # antes/después si hay más de una corrida de esta temporada
        if len(this_season) >= 2:
            run_old = min(r["id"] for r in this_season)
            result["before_after"] = before_after(conn, run_old, run_id)
        return result
    finally:
        conn.close()


def _col_stats(rows, col):
    xs = [r[col] for r in rows if r[col] is not None]
    if not xs:
        return None
    n = len(xs)
    m = sum(xs) / n
    return {"n": n, "mean": m, "std": (sum((x - m) ** 2 for x in xs) / n) ** 0.5,
            "min": min(xs), "max": max(xs)}


if __name__ == "__main__":
    results = {"config": {
        "starter_weight": STARTER_WEIGHT, "home_field_advantage": HOME_FIELD_ADVANTAGE,
        "negbin_dispersion": NEGBIN_DISPERSION, "league_ops_default": DEFAULT_LEAGUE_OPS,
        "league_era_default": LEAGUE_AVG_ERA, "recompute_alpha": 1.0,
    }, "seasons": {}}
    for arg in sys.argv[1:]:
        season_str, db_path = arg.split("=", 1)
        season = int(season_str)
        print(f"... auditando {season} ({db_path})", file=sys.stderr, flush=True)
        try:
            results["seasons"][season] = audit_season(db_path, season)
        except Exception as e:
            import traceback
            results["seasons"][season] = {"season": season, "error": f"{e}", "trace": traceback.format_exc()[-2000:]}
    # gzip+base64 en UNA línea: el log de Actions agrega timestamp por línea
    # y el lector de logs del agente solo puede traer la cola del log --
    # comprimido, el JSON completo entra holgado en cualquier ventana.
    import base64
    import gzip
    payload = base64.b64encode(gzip.compress(json.dumps(results, default=str).encode())).decode()
    print("===JSON_SCI_AUDIT_B64GZ_START===")
    for i in range(0, len(payload), 60000):
        print(payload[i:i + 60000])
    print("===JSON_SCI_AUDIT_B64GZ_END===")
