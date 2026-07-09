"""
Análisis específico de diferencia de carreras (home_proj_runs/away_proj_runs
vs. resultado real) -- MAE/RMSE/bias/R²/Pearson/Spearman, distribución del
error, histograma, QQ plot y scatter plot, con desgloses por favorito,
local/visitante y temperatura.

Los gráficos se guardan como PNG en disco (matplotlib, backend 'Agg' --
sin GUI, apto para correr en CI/servidor) -- nunca se muestran inline ni
dependen de un entorno gráfico.
"""

import math
import os
import statistics

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from historical_engine.db import HistoricalAnalysis, HistoricalGame, SessionLocal
from historical_engine.stats_utils import mae, rmse, bias, pearson_corr, spearman_corr, r_squared


def _joined_rows(run_id: int, session_factory=None) -> list[dict]:
    session_factory = session_factory or SessionLocal
    session = session_factory()
    try:
        analyses = session.query(HistoricalAnalysis).filter_by(run_id=run_id).all()
        games_by_pk = {g.game_pk: g for g in session.query(HistoricalGame).filter_by(run_id=run_id).all()}
    finally:
        session.close()

    rows = []
    for a in analyses:
        g = games_by_pk.get(a.game_pk)
        if g is None or g.home_score is None or g.away_score is None:
            continue
        if a.away_proj_runs is None or a.home_proj_runs is None:
            continue
        rows.append({
            "game_pk": a.game_pk,
            "home_proj": a.home_proj_runs, "away_proj": a.away_proj_runs,
            "home_actual": g.home_score, "away_actual": g.away_score,
            "temp_f": a.temp_f,
            "favorite_is_home": a.home_skellam_prob is not None and a.home_skellam_prob > 0.5,
        })
    return rows


def analyze_runs_projection(run_id: int, output_dir: str, session_factory=None) -> dict:
    """
    Calcula todas las métricas de diferencia de carreras y guarda 3 PNG
    (histograma del error, QQ plot, scatter proyectado-vs-real) en
    `output_dir`. Devuelve el dict de métricas + las rutas de los PNG.
    """
    rows = _joined_rows(run_id, session_factory)
    os.makedirs(output_dir, exist_ok=True)

    if not rows:
        return {"n": 0, "warning": "Sin juegos con proyección y resultado real -- nada que analizar."}

    home_proj = [r["home_proj"] for r in rows]
    home_actual = [r["home_actual"] for r in rows]
    away_proj = [r["away_proj"] for r in rows]
    away_actual = [r["away_actual"] for r in rows]

    # Total combinado (home+away) -- la magnitud que de verdad le importa a
    # Totales, ver model/markets.py::fair_total_line.
    total_proj = [h + a for h, a in zip(home_proj, away_proj)]
    total_actual = [h + a for h, a in zip(home_actual, away_actual)]
    errors = [p - a for p, a in zip(total_proj, total_actual)]

    metrics = {
        "n": len(rows),
        "home_mae": mae(home_proj, home_actual), "home_rmse": rmse(home_proj, home_actual),
        "home_bias": bias(home_proj, home_actual),
        "away_mae": mae(away_proj, away_actual), "away_rmse": rmse(away_proj, away_actual),
        "away_bias": bias(away_proj, away_actual),
        "total_mae": mae(total_proj, total_actual), "total_rmse": rmse(total_proj, total_actual),
        "total_bias": bias(total_proj, total_actual),
        "total_r2": r_squared(total_proj, total_actual),
        "total_pearson": pearson_corr(total_proj, total_actual),
        "total_spearman": spearman_corr(total_proj, total_actual),
    }

    # Desglose por favorito / local / visitante / temperatura -- mismo
    # criterio de "nunca inventar": si no hay suficientes filas en un grupo
    # (n<5), se reporta None en vez de una cifra sobre una muestra ínfima.
    favorite_rows = [r for r in rows if r["favorite_is_home"]]
    underdog_rows = [r for r in rows if not r["favorite_is_home"]]
    hot_rows = [r for r in rows if r["temp_f"] is not None and r["temp_f"] >= 80]
    cold_rows = [r for r in rows if r["temp_f"] is not None and r["temp_f"] < 60]

    def _group_mae(group_rows):
        if len(group_rows) < 5:
            return None
        gp = [rr["home_proj"] + rr["away_proj"] for rr in group_rows]
        ga = [rr["home_actual"] + rr["away_actual"] for rr in group_rows]
        return mae(gp, ga)

    metrics["breakdown"] = {
        "favorito_local_mae": _group_mae(favorite_rows), "n_favorito_local": len(favorite_rows),
        "favorito_visitante_mae": _group_mae(underdog_rows), "n_favorito_visitante": len(underdog_rows),
        "clima_caluroso_mae": _group_mae(hot_rows), "n_clima_caluroso": len(hot_rows),
        "clima_frio_mae": _group_mae(cold_rows), "n_clima_frio": len(cold_rows),
    }

    metrics["plots"] = {
        "histogram": _plot_histogram(errors, output_dir),
        "qq_plot": _plot_qq(errors, output_dir),
        "scatter": _plot_scatter(total_proj, total_actual, output_dir),
    }
    return metrics


def _plot_histogram(errors: list[float], output_dir: str) -> str:
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hist(errors, bins=20, color="#2f6fb0", edgecolor="white")
    ax.set_title("Distribución del error (carreras totales proyectadas - reales)")
    ax.set_xlabel("Error (proyectado - real)")
    ax.set_ylabel("Frecuencia")
    ax.axvline(0, color="#b3362b", linestyle="--", linewidth=1)
    path = os.path.join(output_dir, "runs_error_histogram.png")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def _plot_qq(errors: list[float], output_dir: str) -> str:
    """QQ plot contra una normal teórica, implementado sin scipy.stats.probplot
    (evita atar este módulo a una versión específica de scipy) -- cuantiles
    muestrales ordenados vs. cuantiles normales teóricos vía la aproximación
    racional de Acklam para la inversa de la normal estándar."""
    n = len(errors)
    sorted_errors = sorted(errors)
    mean_e = statistics.mean(errors)
    std_e = statistics.pstdev(errors) if n > 1 else 1.0
    std_e = std_e or 1.0

    theoretical_quantiles = [_inv_normal_cdf((i - 0.5) / n) for i in range(1, n + 1)]
    standardized_sample = [(x - mean_e) / std_e for x in sorted_errors]

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.scatter(theoretical_quantiles, standardized_sample, s=14, color="#2f6fb0", alpha=0.8)
    lims = [min(theoretical_quantiles + standardized_sample), max(theoretical_quantiles + standardized_sample)]
    ax.plot(lims, lims, color="#b3362b", linestyle="--", linewidth=1)
    ax.set_title("QQ Plot del error (estandarizado) vs. Normal teórica")
    ax.set_xlabel("Cuantiles teóricos (Normal)")
    ax.set_ylabel("Cuantiles muestrales (estandarizados)")
    path = os.path.join(output_dir, "runs_error_qq_plot.png")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def _inv_normal_cdf(p: float) -> float:
    """Aproximación racional de Acklam para la inversa de la CDF normal
    estándar -- precisión suficiente para un QQ plot (no para inferencia
    de alta precisión), sin depender de scipy.stats.norm.ppf."""
    if p <= 0:
        p = 1e-10
    if p >= 1:
        p = 1 - 1e-10
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    p_low, p_high = 0.02425, 1 - 0.02425

    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > p_high:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _plot_scatter(proj: list[float], actual: list[float], output_dir: str) -> str:
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.scatter(proj, actual, s=16, color="#2f6fb0", alpha=0.7)
    lims = [min(proj + actual), max(proj + actual)]
    ax.plot(lims, lims, color="#b3362b", linestyle="--", linewidth=1, label="Predicción perfecta")
    ax.set_xlabel("Carreras totales proyectadas")
    ax.set_ylabel("Carreras totales reales")
    ax.set_title("Proyectado vs. Real (carreras totales por juego)")
    ax.legend()
    path = os.path.join(output_dir, "runs_projected_vs_actual_scatter.png")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path
