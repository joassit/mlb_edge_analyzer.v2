"""
Auditoría científica AVANZADA (segunda ronda): incertidumbre de métricas
(bootstrap), robustez de las mejoras propuestas (LOSO), estabilidad de
parámetros entre temporadas, interacciones entre variables (screening por
residuales), techo predictivo (logística LOSO + isotónica + piso de ruido),
incertidumbre por predicción (kNN de juegos similares) y riesgo de
sobreajuste. Corre server-side contra las 4 historical_backtest.db reales.

Uso: python scratchpad_advanced_audit.py 2022=... 2023=... 2024=... 2025=...
Salida: JSON gzip+base64 entre ===JSON_ADV_AUDIT_B64GZ_START/END===.
"""

import base64
import gzip
import json
import math
import sqlite3
import sys

import numpy as np

sys.path.insert(0, ".")

from config import (
    STARTER_WEIGHT, HOME_FIELD_ADVANTAGE, PARK_FACTOR_WEIGHT,
    WEATHER_CORRECTION, NEGBIN_DISPERSION,
)
from model.predictor import predict_from_raw_inputs
from model.negbin_model import negbin_win_prob
from model.runs_projection import LEAGUE_AVG_ERA, LEAGUE_AVG_RUNS_PER_GAME

DEFAULT_LEAGUE_OPS = 0.715
SEASONS = [2022, 2023, 2024, 2025]
RNG = np.random.default_rng(42)
B_BOOT = 2000
B_BOOT_ECE = 500

FEATURE_NAMES = ["era_diff", "ops_diff", "bp_diff", "kpct_diff", "bbpct_diff",
                 "rest_diff", "park", "temp"]


def q(conn, sql, args=()):
    cur = conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def load_season(db_path, season):
    conn = sqlite3.connect(db_path)
    runs = q(conn, "SELECT id FROM historical_run WHERE run_type='season' AND season_year=? ORDER BY id", (season,))
    run_id = max(r["id"] for r in runs)
    rows = q(conn, """
        SELECT a.game_pk, a.game_date, a.away_era, a.home_era,
               a.away_innings_pitched, a.home_innings_pitched,
               a.away_ops, a.home_ops, a.away_team_pa, a.home_team_pa,
               a.away_bullpen_era, a.home_bullpen_era,
               a.away_k_pct, a.home_k_pct, a.away_bb_pct, a.home_bb_pct,
               a.away_days_rest, a.home_days_rest, a.park_factor, a.temp_f,
               a.away_proj_runs, a.home_proj_runs,
               a.home_model_prob, a.home_skellam_prob, a.home_negbin_prob,
               g.home_score, g.away_score, g.winner
        FROM historical_analysis a
        JOIN historical_game g ON g.game_pk = a.game_pk AND g.run_id = a.run_id
        WHERE a.run_id = ? AND g.winner IS NOT NULL
          AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL
          AND a.home_skellam_prob IS NOT NULL AND a.home_model_prob IS NOT NULL
    """, (run_id,))
    conn.close()
    return rows


def col(rows, name, default=np.nan):
    return np.array([r[name] if r[name] is not None else default for r in rows], dtype=float)


def build_arrays(rows):
    d = {
        "y": np.array([1.0 if r["winner"] == "home" else 0.0 for r in rows]),
        "p_sk": col(rows, "home_skellam_prob"),
        "p_he": col(rows, "home_model_prob"),
        "p_nb": col(rows, "home_negbin_prob"),
        "mu_h": col(rows, "home_proj_runs"),
        "mu_a": col(rows, "away_proj_runs"),
        "margin": col(rows, "home_score") - col(rows, "away_score"),
        "total": col(rows, "home_score") + col(rows, "away_score"),
    }
    d["proj_margin"] = d["mu_h"] - d["mu_a"]
    d["proj_total"] = d["mu_h"] + d["mu_a"]
    d["era_diff"] = col(rows, "away_era") - col(rows, "home_era")
    d["ops_diff"] = col(rows, "home_ops") - col(rows, "away_ops")
    d["bp_diff"] = col(rows, "away_bullpen_era") - col(rows, "home_bullpen_era")
    d["kpct_diff"] = col(rows, "home_k_pct") - col(rows, "away_k_pct")
    d["bbpct_diff"] = col(rows, "away_bb_pct") - col(rows, "home_bb_pct")
    d["rest_diff"] = col(rows, "home_days_rest") - col(rows, "away_days_rest")
    d["park"] = col(rows, "park_factor")
    d["temp"] = col(rows, "temp_f")
    return d


def raw_inputs(r, starter_weight=STARTER_WEIGHT, hfa=HOME_FIELD_ADVANTAGE, ops_shrink_k=None):
    req = (r["away_era"], r["home_era"], r["away_ops"], r["home_ops"],
           r["away_bullpen_era"], r["home_bullpen_era"], r["park_factor"])
    if any(v is None for v in req):
        return None
    away_ops, home_ops = r["away_ops"], r["home_ops"]
    if ops_shrink_k:
        apa = r["away_team_pa"] or 0
        hpa = r["home_team_pa"] or 0
        away_ops = (away_ops * apa + DEFAULT_LEAGUE_OPS * ops_shrink_k) / (apa + ops_shrink_k)
        home_ops = (home_ops * hpa + DEFAULT_LEAGUE_OPS * ops_shrink_k) / (hpa + ops_shrink_k)
    return {
        "away_era": r["away_era"], "home_era": r["home_era"],
        "away_ops": away_ops, "home_ops": home_ops,
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
        "home_field_advantage": hfa, "negbin_dispersion": NEGBIN_DISPERSION,
        "skellam_shrinkage_alpha": 1.0,
    }


def recompute_pass(rows, **kw):
    """Devuelve (p_skellam, p_heuristic, mask) recomputados con el motor real."""
    ps, ph, ok = [], [], []
    for r in rows:
        ri = raw_inputs(r, **kw)
        if ri is None:
            ps.append(np.nan); ph.append(np.nan); ok.append(False)
            continue
        pred = predict_from_raw_inputs(ri)
        ps.append(pred["home_skellam_prob"]); ph.append(pred["home_model_prob"]); ok.append(True)
    return np.array(ps), np.array(ph), np.array(ok)


# ---------- métricas ----------

def brier(p, y):
    return float(np.mean((p - y) ** 2))


def logloss(p, y):
    pc = np.clip(p, 1e-12, 1 - 1e-12)
    return float(-np.mean(y * np.log(pc) + (1 - y) * np.log(1 - pc)))


def accuracy(p, y):
    return float(np.mean((p >= 0.5) == (y == 1)))


def sharp(p):
    return float(np.mean(np.abs(p - 0.5)))


def ece6(p, y):
    conf = np.maximum(p, 1 - p)
    hit = ((p >= 0.5) == (y == 1)).astype(float)
    edges = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 1.01]
    n = len(p)
    total = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf >= lo) & (conf < hi)
        if m.sum() == 0:
            continue
        total += abs(hit[m].mean() - conf[m].mean()) * m.sum()
    return float(total / n)


def boot_ci(values_fn, n, B=B_BOOT, seed=0):
    """values_fn(idx)->float. Devuelve dict con point/se/ci95/ci99/hist."""
    rng = np.random.default_rng(seed)
    stats = np.empty(B)
    for b in range(B):
        idx = rng.integers(0, n, n)
        stats[b] = values_fn(idx)
    point = values_fn(np.arange(n))
    lo95, hi95 = np.percentile(stats, [2.5, 97.5])
    lo99, hi99 = np.percentile(stats, [0.5, 99.5])
    hist, edges = np.histogram(stats, bins=15)
    return {"point": float(point), "se": float(stats.std(ddof=1)),
            "ci95": [float(lo95), float(hi95)], "ci99": [float(lo99), float(hi99)],
            "boot_hist": {"counts": hist.tolist(), "lo": float(edges[0]), "hi": float(edges[-1])}}


def mcnemar(c1, c2):
    b = int(np.sum(c1 & ~c2)); c = int(np.sum(~c1 & c2))
    if b + c == 0:
        return {"b": b, "c": c, "chi2": None, "p": 1.0}
    chi2 = (abs(b - c) - 1) ** 2 / (b + c)
    from scipy.stats import chi2 as chi2dist
    return {"b": b, "c": c, "chi2": float(chi2), "p": float(1 - chi2dist.cdf(chi2, 1))}


# ---------- FASE 1 ----------

def fase1(d, seed):
    y, n = d["y"], len(d["y"])
    out = {}
    specs = {
        "accuracy_skellam": lambda i: accuracy(d["p_sk"][i], y[i]),
        "accuracy_heuristico": lambda i: accuracy(d["p_he"][i], y[i]),
        "brier_skellam": lambda i: brier(d["p_sk"][i], y[i]),
        "brier_heuristico": lambda i: brier(d["p_he"][i], y[i]),
        "brier_negbin": lambda i: brier(d["p_nb"][i], y[i]),
        "logloss_skellam": lambda i: logloss(d["p_sk"][i], y[i]),
        "sharpness_skellam": lambda i: sharp(d["p_sk"][i]),
        "mae_margen": lambda i: float(np.mean(np.abs(d["proj_margin"][i] - d["margin"][i]))),
        "rmse_margen": lambda i: float(np.sqrt(np.mean((d["proj_margin"][i] - d["margin"][i]) ** 2))),
        "mae_total": lambda i: float(np.mean(np.abs(d["proj_total"][i] - d["total"][i]))),
    }
    for name, fn in specs.items():
        out[name] = boot_ci(fn, n, seed=seed)
    out["ece_skellam"] = boot_ci(lambda i: ece6(d["p_sk"][i], y[i]), n, B=B_BOOT_ECE, seed=seed + 1)
    out["ece_heuristico"] = boot_ci(lambda i: ece6(d["p_he"][i], y[i]), n, B=B_BOOT_ECE, seed=seed + 2)

    # diferencias pareadas heurístico vs skellam
    dif = (d["p_he"] - y) ** 2 - (d["p_sk"] - y) ** 2
    out["delta_brier_heu_menos_sk"] = boot_ci(lambda i: float(np.mean(dif[i])), n, seed=seed + 3)
    c_he = (d["p_he"] >= 0.5) == (y == 1)
    c_sk = (d["p_sk"] >= 0.5) == (y == 1)
    out["mcnemar_heu_vs_sk"] = mcnemar(c_he, c_sk)
    return out


# ---------- FASE 2/3: barridos y LOSO ----------

def alpha_sweep(all_d):
    alphas = np.round(np.arange(0.10, 1.001, 0.05), 2)
    per = {}
    for s, d in all_d.items():
        briers = [brier(0.5 + a * (d["p_sk"] - 0.5), d["y"]) for a in alphas]
        per[s] = {"alphas": alphas.tolist(), "briers": briers,
                  "opt": float(alphas[int(np.argmin(briers))])}
    loso = {}
    for held in SEASONS:
        train = [s for s in SEASONS if s != held]
        tot = np.zeros(len(alphas))
        for s in train:
            tot += np.array(per[s]["briers"]) * len(all_d[s]["y"])
        a_star = float(alphas[int(np.argmin(tot))])
        d = all_d[held]
        delta = brier(0.5 + a_star * (d["p_sk"] - 0.5), d["y"]) - brier(d["p_sk"], d["y"])
        # CI bootstrap del delta pareado
        dif = (0.5 + a_star * (d["p_sk"] - 0.5) - d["y"]) ** 2 - (d["p_sk"] - d["y"]) ** 2
        ci = boot_ci(lambda i: float(np.mean(dif[i])), len(dif), B=1000, seed=7)
        loso[held] = {"alpha_entrenado": a_star, "delta_brier_vs_sin_calibrar": float(delta),
                      "ci95": ci["ci95"]}
    return {"per_season": per, "loso": loso}


def negbin_k_sweep(all_d):
    ks = [2.0, 3.0, 4.0, 5.0, 7.0, 9.0, 12.0]
    per = {}
    probs_cache = {}
    for s, d in all_d.items():
        briers = []
        probs_cache[s] = {}
        for k in ks:
            p = np.array([negbin_win_prob(h, a, k) for h, a in zip(d["mu_h"], d["mu_a"])])
            probs_cache[s][k] = p
            briers.append(brier(p, d["y"]))
        per[s] = {"ks": ks, "briers": briers, "opt": float(ks[int(np.argmin(briers))])}
    loso = {}
    for held in SEASONS:
        train = [s for s in SEASONS if s != held]
        tot = np.zeros(len(ks))
        for s in train:
            tot += np.array(per[s]["briers"]) * len(all_d[s]["y"])
        k_star = float(ks[int(np.argmin(tot))])
        d = all_d[held]
        dif = (probs_cache[held][k_star] - d["y"]) ** 2 - (probs_cache[held][7.0] - d["y"]) ** 2
        ci = boot_ci(lambda i: float(np.mean(dif[i])), len(dif), B=1000, seed=8)
        loso[held] = {"k_entrenado": k_star, "delta_brier_vs_k7": float(np.mean(dif)), "ci95": ci["ci95"]}
    # efecto k=3 vs 7 por temporada con CI
    efecto_k3 = {}
    for s, d in all_d.items():
        dif = (probs_cache[s][3.0] - d["y"]) ** 2 - (probs_cache[s][7.0] - d["y"]) ** 2
        ci = boot_ci(lambda i: float(np.mean(dif[i])), len(dif), B=1000, seed=9)
        efecto_k3[s] = {"delta": float(np.mean(dif)), "ci95": ci["ci95"]}
    return {"per_season": per, "loso": loso, "efecto_k3_vs_k7": efecto_k3}


def sweep_recompute(all_rows, all_d):
    """Barridos que exigen recomputar con el motor real: starter_weight,
    shrink de OPS y HFA (heurístico)."""
    weights = [0.35, 0.45, 0.55, 0.65, 0.75]
    ops_ks = [150, 300, 600]
    hfas = [0.0, 0.04, 0.06]
    out = {"starter_weight": {}, "ops_shrink": {}, "hfa": {}}
    cache_w, cache_ops = {}, {}
    for s, rows in all_rows.items():
        d = all_d[s]
        y = d["y"]
        briers_w = []
        cache_w[s] = {}
        for w in weights:
            ps, _, ok = recompute_pass(rows, starter_weight=w)
            briers_w.append(brier(ps[ok], y[ok]))
            cache_w[s][w] = (ps, ok)
        out["starter_weight"][s] = {"valores": weights, "briers": briers_w,
                                    "opt": float(weights[int(np.argmin(briers_w))])}
        briers_o = []
        base = cache_w[s][0.65]
        cache_ops[s] = {0: base}
        for kpa in ops_ks:
            ps, _, ok = recompute_pass(rows, ops_shrink_k=kpa)
            briers_o.append(brier(ps[ok], y[ok]))
            cache_ops[s][kpa] = (ps, ok)
        out["ops_shrink"][s] = {"valores": [0] + ops_ks,
                                "briers": [brier(base[0][base[1]], y[base[1]])] + briers_o,
                                "opt": float(([0] + ops_ks)[int(np.argmin([brier(base[0][base[1]], y[base[1]])] + briers_o))])}
        briers_h = []
        for hfa in hfas:
            _, ph, ok = recompute_pass(rows, hfa=hfa)
            briers_h.append(brier(ph[ok], y[ok]))
        base_h = recompute_pass(rows)  # hfa=0.02
        vals_h = [0.0, 0.02, 0.04, 0.06]
        briers_h = [briers_h[0], brier(base_h[1][base_h[2]], y[base_h[2]]), briers_h[1], briers_h[2]]
        out["hfa"][s] = {"valores": vals_h, "briers": briers_h,
                         "opt": float(vals_h[int(np.argmin(briers_h))])}
    # LOSO starter_weight (mejora vs 0.65)
    loso_w = {}
    for held in SEASONS:
        train = [s for s in SEASONS if s != held]
        tot = np.zeros(len(weights))
        for s in train:
            tot += np.array(out["starter_weight"][s]["briers"]) * len(all_d[s]["y"])
        w_star = weights[int(np.argmin(tot))]
        ps_star, ok_s = cache_w[held][w_star]
        ps_base, ok_b = cache_w[held][0.65]
        ok = ok_s & ok_b
        dif = (ps_star[ok] - all_d[held]["y"][ok]) ** 2 - (ps_base[ok] - all_d[held]["y"][ok]) ** 2
        ci = boot_ci(lambda i: float(np.mean(dif[i])), len(dif), B=1000, seed=10)
        loso_w[held] = {"w_entrenado": w_star, "delta_brier_vs_065": float(np.mean(dif)), "ci95": ci["ci95"]}
    out["starter_weight_loso"] = loso_w
    # LOSO ops shrink
    loso_o = {}
    ops_vals = [0, 150, 300, 600]
    for held in SEASONS:
        train = [s for s in SEASONS if s != held]
        tot = np.zeros(len(ops_vals))
        for s in train:
            tot += np.array(out["ops_shrink"][s]["briers"]) * len(all_d[s]["y"])
        k_star = ops_vals[int(np.argmin(tot))]
        ps_star, ok_s = cache_ops[held][k_star]
        ps_base, ok_b = cache_ops[held][0]
        ok = ok_s & ok_b
        dif = (ps_star[ok] - all_d[held]["y"][ok]) ** 2 - (ps_base[ok] - all_d[held]["y"][ok]) ** 2
        ci = boot_ci(lambda i: float(np.mean(dif[i])), len(dif), B=1000, seed=11)
        loso_o[held] = {"k_pa_entrenado": k_star, "delta_brier_vs_sin_shrink": float(np.mean(dif)), "ci95": ci["ci95"]}
    out["ops_shrink_loso"] = loso_o
    return out


def fav_away_effect(all_d):
    out = {}
    for s, d in all_d.items():
        fh = d["p_sk"] >= 0.5
        y = d["y"]
        dif_fn = lambda i: brier(d["p_sk"][i][~fh[i]], y[i][~fh[i]]) - brier(d["p_sk"][i][fh[i]], y[i][fh[i]]) \
            if (~fh[i]).sum() > 30 and fh[i].sum() > 30 else np.nan
        # bootstrap manual
        rng = np.random.default_rng(12)
        stats = []
        n = len(y)
        for _ in range(1000):
            i = rng.integers(0, n, n)
            m = fh[i]
            if m.sum() < 30 or (~m).sum() < 30:
                continue
            stats.append(brier(d["p_sk"][i][~m], y[i][~m]) - brier(d["p_sk"][i][m], y[i][m]))
        stats = np.array(stats)
        point = brier(d["p_sk"][~fh], y[~fh]) - brier(d["p_sk"][fh], y[fh])
        out[s] = {"gap_brier_visitante_menos_local": float(point),
                  "ci95": [float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))],
                  "acc_fav_local": accuracy(d["p_sk"][fh], y[fh]),
                  "acc_fav_visitante": accuracy(d["p_sk"][~fh], y[~fh]),
                  "n_local": int(fh.sum()), "n_visitante": int((~fh).sum())}
    return out


def totals_bias_ci(all_d):
    out = {}
    for s, d in all_d.items():
        err = d["proj_total"] - d["total"]
        ci = boot_ci(lambda i: float(np.mean(err[i])), len(err), B=1000, seed=13)
        out[s] = {"bias": ci["point"], "ci95": ci["ci95"]}
    return out


# ---------- FASE 4: interacciones ----------

def standardize(x):
    m = np.nanmean(x); sd = np.nanstd(x)
    z = (x - m) / (sd if sd > 0 else 1.0)
    return np.nan_to_num(z, nan=0.0)


def interactions(all_d):
    pairs = []
    for i in range(len(FEATURE_NAMES)):
        for j in range(i + 1, len(FEATURE_NAMES)):
            pairs.append((FEATURE_NAMES[i], FEATURE_NAMES[j]))
    per_pair = {}
    main_res = {}
    for s, d in all_d.items():
        r = d["y"] - d["p_sk"]
        var_r = float(np.var(r))
        Z = {f: standardize(d[f]) for f in FEATURE_NAMES}
        for f in FEATURE_NAMES:
            c = float(np.corrcoef(Z[f], r)[0, 1])
            main_res.setdefault(f, {})[s] = c
        for a, b in pairs:
            term = standardize(Z[a] * Z[b])
            c = float(np.corrcoef(term, r)[0, 1])
            per_pair.setdefault(f"{a}*{b}", {})[s] = {"corr": c, "delta_brier_max": c * c * var_r}
    # ranking: |corr| medio y consistencia de signo
    ranked = []
    for pair, by_s in per_pair.items():
        cs = [by_s[s]["corr"] for s in SEASONS]
        same_sign = max(sum(1 for c in cs if c > 0), sum(1 for c in cs if c < 0))
        ranked.append({"pair": pair, "corr_media_abs": float(np.mean(np.abs(cs))),
                       "corrs": {s: round(by_s[s]["corr"], 4) for s in SEASONS},
                       "consistencia_signo": int(same_sign),
                       "delta_brier_max_medio": float(np.mean([by_s[s]["delta_brier_max"] for s in SEASONS]))})
    ranked.sort(key=lambda x: -x["corr_media_abs"])
    return {"interacciones_top": ranked[:12], "residual_vs_variable_principal": main_res}


# ---------- FASE 5: techo predictivo ----------

def logistic_irls(X, y, ridge=1e-3, iters=30):
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        z = X @ w
        p = 1 / (1 + np.exp(-np.clip(z, -30, 30)))
        W = p * (1 - p) + 1e-9
        g = X.T @ (y - p) - ridge * w
        H = (X.T * W) @ X + ridge * np.eye(X.shape[1])
        try:
            w += np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
    return w


def build_X(d, feats, means=None, stds=None, inter_pairs=None):
    cols = []
    for f in feats:
        cols.append(d[f].copy())
    X = np.column_stack(cols)
    if means is None:
        means = np.nanmean(X, axis=0); stds = np.nanstd(X, axis=0); stds[stds == 0] = 1
    X = (X - means) / stds
    X = np.nan_to_num(X, nan=0.0)
    if inter_pairs:
        extra = [X[:, feats.index(a)] * X[:, feats.index(b)] for a, b in inter_pairs]
        X = np.column_stack([X] + extra)
    return np.column_stack([np.ones(len(X)), X]), means, stds


def ceiling(all_d, top_pairs):
    res = {"logistic_loso": {}, "logistic_inter_loso": {}, "isotonic_loso": {},
           "isotonic_insample": {}, "margen_lineal_loso": {}}
    inter = [tuple(p["pair"].split("*")) for p in top_pairs[:5]]
    for held in SEASONS:
        train = [s for s in SEASONS if s != held]
        Xtr_raw = {f: np.concatenate([all_d[s][f] for s in train]) for f in FEATURE_NAMES}
        ytr = np.concatenate([all_d[s]["y"] for s in train])
        Xtr, mu, sd = build_X(Xtr_raw, FEATURE_NAMES)
        Xte, _, _ = build_X(all_d[held], FEATURE_NAMES, mu, sd)
        w = logistic_irls(Xtr, ytr)
        p = 1 / (1 + np.exp(-np.clip(Xte @ w, -30, 30)))
        y = all_d[held]["y"]
        res["logistic_loso"][held] = {"acc": accuracy(p, y), "brier": brier(p, y),
                                      "logloss": logloss(p, y), "ece": ece6(p, y),
                                      "acc_max_teorico": float(np.mean(np.maximum(p, 1 - p)))}
        Xtr2, mu2, sd2 = build_X(Xtr_raw, FEATURE_NAMES, inter_pairs=inter)
        Xte2, _, _ = build_X(all_d[held], FEATURE_NAMES, mu2, sd2, inter_pairs=inter)
        w2 = logistic_irls(Xtr2, ytr)
        p2 = 1 / (1 + np.exp(-np.clip(Xte2 @ w2, -30, 30)))
        res["logistic_inter_loso"][held] = {"acc": accuracy(p2, y), "brier": brier(p2, y)}
        # isotónica LOSO sobre p_sk
        p_tr = np.concatenate([all_d[s]["p_sk"] for s in train])
        cal = pav_fit(p_tr, ytr)
        p_iso = pav_apply(cal, all_d[held]["p_sk"])
        res["isotonic_loso"][held] = {"brier": brier(p_iso, y),
                                      "brier_sin_calibrar": brier(all_d[held]["p_sk"], y)}
        # margen lineal LOSO
        Xm = Xtr[:, :]
        mtr = np.concatenate([all_d[s]["margin"] for s in train])
        beta, *_ = np.linalg.lstsq(Xm, mtr, rcond=None)
        pred_m = Xte @ beta
        m = all_d[held]["margin"]
        ss_res = float(np.sum((m - pred_m) ** 2)); ss_tot = float(np.sum((m - m.mean()) ** 2))
        r2 = 1 - ss_res / ss_tot
        res["margen_lineal_loso"][held] = {"r2": r2,
                                           "rmse": float(np.sqrt(np.mean((m - pred_m) ** 2))),
                                           "rmse_piso_estimado": float(np.std(m) * math.sqrt(max(1 - max(r2, 0), 0)))}
    for s, d in all_d.items():
        cal = pav_fit(d["p_sk"], d["y"])
        p_iso = pav_apply(cal, d["p_sk"])
        res["isotonic_insample"][s] = {"brier": brier(p_iso, d["y"]),
                                       "brier_sin_calibrar": brier(d["p_sk"], d["y"])}
    return res


def pav_fit(p, y):
    order = np.argsort(p)
    ps, ys = p[order], y[order]
    # pool adjacent violators sobre bloques
    vals = ys.astype(float).tolist()
    wts = [1.0] * len(vals)
    xs = ps.tolist()
    i = 0
    v, w_, x0, x1 = [], [], [], []
    for j in range(len(vals)):
        v.append(vals[j]); w_.append(1.0); x0.append(xs[j]); x1.append(xs[j])
        while len(v) > 1 and v[-2] > v[-1]:
            wv = w_[-2] + w_[-1]
            v[-2] = (v[-2] * w_[-2] + v[-1] * w_[-1]) / wv
            w_[-2] = wv; x1[-2] = x1[-1]
            v.pop(); w_.pop(); x0.pop(); x1.pop()
    return {"x0": x0, "x1": x1, "v": v}


def pav_apply(cal, p):
    x0 = np.array(cal["x0"]); v = np.array(cal["v"])
    idx = np.searchsorted(x0, p, side="right") - 1
    idx = np.clip(idx, 0, len(v) - 1)
    return v[idx]


# ---------- FASE 6/7: incertidumbre por predicción (kNN) ----------

def knn_evidence(all_d, sample_per_season=250, k=200):
    feats = ["era_diff", "ops_diff", "bp_diff", "park", "temp"]
    Z = {}
    allz = np.concatenate([np.column_stack([all_d[s][f] for f in feats]) for s in SEASONS])
    mu = np.nanmean(allz, axis=0); sd = np.nanstd(allz, axis=0); sd[sd == 0] = 1
    for s in SEASONS:
        M = np.column_stack([all_d[s][f] for f in feats])
        Z[s] = np.nan_to_num((M - mu) / sd, nan=0.0)
    rng = np.random.default_rng(21)
    recs = []
    for s in SEASONS:
        others = [t for t in SEASONS if t != s]
        Zo = np.concatenate([Z[t] for t in others])
        yo = np.concatenate([all_d[t]["y"] for t in others])
        n_s = len(all_d[s]["y"])
        pick = rng.choice(n_s, size=min(sample_per_season, n_s), replace=False)
        for i in pick:
            dist = np.sum((Zo - Z[s][i]) ** 2, axis=1)
            nn = np.argpartition(dist, k)[:k]
            rate = float(yo[nn].mean())
            p = float(all_d[s]["p_sk"][i])
            y = float(all_d[s]["y"][i])
            recs.append({"disagree": abs(p - rate), "brier_game": (p - y) ** 2,
                         "p": p, "rate": rate, "hit": float((p >= 0.5) == (y == 1)),
                         "mean_nn_dist": float(np.sqrt(dist[nn]).mean())})
    dis = np.array([r["disagree"] for r in recs])
    bg = np.array([r["brier_game"] for r in recs])
    hit = np.array([r["hit"] for r in recs])
    corr = float(np.corrcoef(dis, bg)[0, 1])
    t1, t2 = np.percentile(dis, [33.3, 66.7])
    def seg(m):
        return {"n": int(m.sum()), "brier": float(bg[m].mean()), "acc": float(hit[m].mean()),
                "disagree_medio": float(dis[m].mean())}
    return {"n_muestra": len(recs), "corr_desacuerdo_vs_brier": corr,
            "terciles": {"acuerdo_alto": seg(dis <= t1),
                         "acuerdo_medio": seg((dis > t1) & (dis <= t2)),
                         "acuerdo_bajo": seg(dis > t2)},
            "umbral_terciles": [float(t1), float(t2)]}


# ---------- main ----------

if __name__ == "__main__":
    paths = dict(arg.split("=", 1) for arg in sys.argv[1:])
    all_rows, all_d = {}, {}
    for s in SEASONS:
        rows = load_season(paths[str(s)], s)
        all_rows[s] = rows
        all_d[s] = build_arrays(rows)
        print(f"... {s}: n={len(rows)}", file=sys.stderr, flush=True)

    out = {"n": {s: len(all_d[s]["y"]) for s in SEASONS}}

    print("... fase 1 (bootstrap)", file=sys.stderr, flush=True)
    out["fase1"] = {s: fase1(all_d[s], seed=100 + s) for s in SEASONS}

    # diferencia entre temporadas (heurístico 2022 vs 2025): z de dos proporciones
    a22 = out["fase1"][2022]["accuracy_heuristico"]["point"]; n22 = out["n"][2022]
    a25 = out["fase1"][2025]["accuracy_heuristico"]["point"]; n25 = out["n"][2025]
    ppool = (a22 * n22 + a25 * n25) / (n22 + n25)
    z = (a22 - a25) / math.sqrt(ppool * (1 - ppool) * (1 / n22 + 1 / n25))
    from scipy.stats import norm
    out["dif_temporadas_acc_heu_2022_vs_2025"] = {"z": z, "p_two_sided": float(2 * (1 - norm.cdf(abs(z))))}

    print("... fase 2 (alpha/k)", file=sys.stderr, flush=True)
    out["alpha"] = alpha_sweep(all_d)
    out["negbin_k"] = negbin_k_sweep(all_d)
    print("... fase 2/3 (recompute sweeps)", file=sys.stderr, flush=True)
    out["sweeps"] = sweep_recompute(all_rows, all_d)
    out["fav_away"] = fav_away_effect(all_d)
    out["totals_bias"] = totals_bias_ci(all_d)

    print("... fase 4 (interacciones)", file=sys.stderr, flush=True)
    out["interacciones"] = interactions(all_d)

    print("... fase 5 (techo)", file=sys.stderr, flush=True)
    out["techo"] = ceiling(all_d, out["interacciones"]["interacciones_top"])

    print("... fase 6/7 (kNN evidencia)", file=sys.stderr, flush=True)
    out["knn"] = knn_evidence(all_d)

    payload = base64.b64encode(gzip.compress(json.dumps(out, default=str).encode())).decode()
    print("===JSON_ADV_AUDIT_B64GZ_START===")
    for i in range(0, len(payload), 60000):
        print(payload[i:i + 60000])
    print("===JSON_ADV_AUDIT_B64GZ_END===")
