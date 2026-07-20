"""
Pruebas de reports/rolling_charts.py -- verifica que el PNG se genere en
disco con datos sembrados a mano y que los paneles vacíos no rompan nada
cuando una serie viene sin puntos (menos juegos/picks que la ventana).
"""

import os

from reports.rolling_charts import plot_rolling_metrics


def _rolling_with_data():
    return {
        "window": 30,
        "brier_series": [
            {"date": f"2026-07-{i:02d}", "n": 30, "rolling_brier": 0.24 + i * 0.001, "rolling_ece": 0.05 + i * 0.001}
            for i in range(1, 11)
        ],
        "roi_series": [
            {"date": f"2026-07-{i:02d}", "n": 30, "rolling_roi": 0.01 * i, "rolling_win_rate": 0.5}
            for i in range(1, 6)
        ],
        "n_games_available": 40,
        "n_real_picks_available": 35,
    }


def _rolling_empty():
    return {"window": 30, "brier_series": [], "roi_series": [], "n_games_available": 5, "n_real_picks_available": 3}


def test_plot_rolling_metrics_saves_png_with_data(tmp_path):
    result = plot_rolling_metrics(_rolling_with_data(), str(tmp_path))

    assert os.path.isfile(result["path"])
    assert result["n_points_brier"] == 10
    assert result["n_points_roi"] == 5


def test_plot_rolling_metrics_handles_empty_series_without_crashing(tmp_path):
    result = plot_rolling_metrics(_rolling_empty(), str(tmp_path))

    assert os.path.isfile(result["path"])
    assert result["n_points_brier"] == 0
    assert result["n_points_roi"] == 0


def test_plot_rolling_metrics_uses_custom_filename(tmp_path):
    result = plot_rolling_metrics(_rolling_with_data(), str(tmp_path), filename="custom_name.png")

    assert result["path"].endswith("custom_name.png")
    assert os.path.isfile(result["path"])
