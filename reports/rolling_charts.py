"""
Gráfico de métricas rodantes (ver tracking.results_tracker.compute_rolling_metrics)
-- PNG guardado en disco (matplotlib, backend 'Agg' -- sin GUI, mismo
patrón que historical_engine/runs_analysis.py), para insertar en el
reporte técnico y detectar drift que un solo promedio agregado diluye.
"""

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def plot_rolling_metrics(rolling: dict, output_dir: str, filename: str = "rolling_metrics.png") -> dict:
    """
    3 paneles apilados: Brier rodante, ECE rodante (ambos del heurístico,
    ventana en JUEGOS) y ROI rodante de picks reales (ventana en PICKS).
    Si una serie viene vacía (todavía no hay `window` juegos/picks), ese
    panel muestra el aviso en vez de un gráfico vacío -- mismo criterio de
    "documentar la ausencia, no inferirla" que el resto de los reportes.
    """
    os.makedirs(output_dir, exist_ok=True)
    brier_series = rolling["brier_series"]
    roi_series = rolling["roi_series"]
    window = rolling["window"]

    fig, axes = plt.subplots(3, 1, figsize=(9, 10.5))

    def _empty_panel(ax, message):
        ax.text(0.5, 0.5, message, ha="center", va="center", transform=ax.transAxes, fontsize=10, color="#666666")
        ax.set_xticks([])
        ax.set_yticks([])

    if brier_series:
        dates = [p["date"] for p in brier_series]
        axes[0].plot(dates, [p["rolling_brier"] for p in brier_series], color="#2563eb", linewidth=1.5)
        axes[0].axhline(0.25, color="#999999", linestyle="--", linewidth=1)
        axes[0].set_title(f"Rolling Brier Score -- heurístico (ventana={window} juegos)")
        axes[0].tick_params(axis="x", rotation=45, labelsize=7)

        axes[1].plot(dates, [p["rolling_ece"] for p in brier_series], color="#7c3aed", linewidth=1.5)
        axes[1].set_title(f"Rolling ECE -- heurístico (ventana={window} juegos)")
        axes[1].tick_params(axis="x", rotation=45, labelsize=7)
    else:
        _empty_panel(axes[0], f"Sin suficientes juegos con resultado para ventana de {window}")
        _empty_panel(axes[1], f"Sin suficientes juegos con resultado para ventana de {window}")

    if roi_series:
        dates = [p["date"] for p in roi_series]
        axes[2].plot(dates, [p["rolling_roi"] * 100 for p in roi_series], color="#059669", linewidth=1.5)
        axes[2].axhline(0.0, color="#999999", linestyle="--", linewidth=1)
        axes[2].set_title(f"Rolling ROI (%) -- picks reales (ventana={window} picks)")
        axes[2].tick_params(axis="x", rotation=45, labelsize=7)
    else:
        _empty_panel(axes[2], f"Sin suficientes picks reales liquidados para ventana de {window}")

    fig.tight_layout()
    out_path = os.path.join(output_dir, filename)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

    return {
        "path": out_path,
        "n_points_brier": len(brier_series),
        "n_points_roi": len(roi_series),
    }
