"""
Reportes históricos -- COMPLETAMENTE separados de reports/generate_report.py
(el reporte oficial de producción). Nunca se importan entre sí. Este
módulo genera un HTML propio (Comparación entre temporadas, drift,
robustez, calibración, distribución de probabilidades, distribución del
margen, diferencia de carreras, y conclusiones automáticas basadas
únicamente en los números ya calculados).
"""

import os
from datetime import datetime, timezone

from historical_engine.model_comparison import compare_models
from historical_engine.validation import compare_seasons_drift, SOURCES
from historical_engine.runs_analysis import analyze_runs_projection

BULLPEN_RISK_WARNING = (
    "Bullpen ERA usa el roster ACTUAL como aproximación del histórico -- sesgo potencial en "
    "cualquier métrica que dependa de bullpen, especialmente pitching_staff_score con "
    "starter_weight=0.65 aplicado a temporadas pasadas. bullpen_era es un dato REQUERIDO para que un "
    "juego se analice (ver historical_engine/pipeline.py) -- todo juego presente en este reporte, sin "
    "excepción, usó esta aproximación."
)

_STYLE = """
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
     background:#f4f6f9;color:#1b2430;margin:0;padding:0;}
.page{max-width:900px;margin:0 auto;background:#fff;padding:0 0 40px;}
.cover{background:linear-gradient(160deg,#0e2340,#16335c);color:#fff;padding:40px 40px 28px;}
.cover h1{margin:0 0 6px;font-size:26px;}
.cover p{margin:0;opacity:.85;font-size:13px;}
.content{padding:20px 40px;}
h2{color:#0e2340;border-bottom:2px solid #0e2340;padding-bottom:6px;font-size:16px;margin-top:34px;}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin:10px 0;}
th,td{padding:6px 8px;border-bottom:1px solid #dde3ea;text-align:left;}
thead th{text-transform:uppercase;font-size:10px;color:#5a6472;border-bottom:2px solid #0e2340;}
.callout{background:#f4f6f9;border-left:3px solid #2f6fb0;padding:10px 14px;font-size:12.5px;margin:10px 0;}
.callout-warning{background:#fdf3e3;border-left:3px solid #c9860f;padding:10px 14px;font-size:12.5px;margin:10px 0;}
.na{color:#8a6a1f;font-weight:600;}
img{max-width:100%;border:1px solid #dde3ea;border-radius:4px;margin:8px 0;}
footer{border-top:2px solid #0e2340;margin-top:40px;padding:14px 40px;font-size:10.5px;color:#8b95a3;}
</style>
"""


def _fmt(value, pct=False, digits=3):
    if value is None:
        return '<span class="na">N/D</span>'
    if pct:
        return f"{value:+.1%}" if isinstance(value, float) and abs(value) < 5 else f"{value}"
    return f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def generate_historical_report(
    season_year: int, run_id: int, output_dir: str,
    other_seasons_for_drift: list[int] | None = None, session_factory=None,
) -> str:
    """
    Genera un reporte HTML autocontenido en `output_dir/historical_report_<season>_<run_id>.html`.
    Devuelve la ruta del archivo. Nunca toca reports/generate_report.py ni
    ninguna tabla de producción -- solo lee de historical_engine.db.
    """
    os.makedirs(output_dir, exist_ok=True)
    comparison = compare_models(season_year, run_id, session_factory)

    drift_by_source = {}
    if other_seasons_for_drift:
        seasons = sorted(set([season_year] + other_seasons_for_drift))
        for source in SOURCES:
            drift_by_source[source] = compare_seasons_drift(source, seasons, session_factory)

    runs_metrics = analyze_runs_projection(run_id, output_dir=output_dir, session_factory=session_factory)

    conclusions = _auto_conclusions(comparison, drift_by_source, runs_metrics)

    html = _render_html(season_year, run_id, comparison, drift_by_source, runs_metrics, conclusions, output_dir)
    path = os.path.join(output_dir, f"historical_report_{season_year}_{run_id}.html")
    with open(path, "w") as f:
        f.write(html)
    return path


def _auto_conclusions(comparison: dict, drift_by_source: dict, runs_metrics: dict) -> list[str]:
    conclusions = list(comparison["observations"])

    for source, drift in drift_by_source.items():
        if drift["drift_flagged"]:
            conclusions.append(
                f"Drift detectado en {source}: variación de accuracy entre temporadas de "
                f"{drift['max_accuracy_spread']:.1%} (por encima del umbral de 15pp documentado)."
            )

    if runs_metrics.get("n", 0) > 0:
        bias = runs_metrics.get("total_bias")
        if bias is not None and abs(bias) > 0.5:
            direction = "sobreproyecta" if bias > 0 else "subproyecta"
            conclusions.append(
                f"El modelo {direction} el total de carreras en promedio {abs(bias):.2f} carreras/juego."
            )
    else:
        conclusions.append("Sin juegos con proyección y resultado real -- no se puede concluir sobre diferencia de carreras.")

    conclusions.append(
        "Feature importance / análisis de sensibilidad: no implementado en esta versión del motor -- "
        "requeriría un modelo entrenable con importancia de variables (ej. un GBM auxiliar), fuera del "
        "alcance de esta entrega. Documentado explícitamente en vez de inventar un número."
    )
    conclusions.append(BULLPEN_RISK_WARNING)
    return conclusions


def _render_html(season_year, run_id, comparison, drift_by_source, runs_metrics, conclusions, output_dir) -> str:
    rows = []
    for source, m in comparison["table"].items():
        rows.append(
            f"<tr><td>{source}</td><td>{_fmt(m.get('n_sample'))}</td>"
            f"<td>{_fmt(m.get('accuracy'), pct=True)}</td>"
            f"<td>{_fmt(m.get('brier_score'))}</td><td>{_fmt(m.get('log_loss'))}</td>"
            f"<td>{_fmt(m.get('ece'), pct=True)}</td><td>{_fmt(m.get('mce'), pct=True)}</td></tr>"
        )

    drift_rows = []
    for source, drift in drift_by_source.items():
        for season, stats in drift["by_season"].items():
            drift_rows.append(
                f"<tr><td>{source}</td><td>{season}</td><td>{_fmt(stats.get('n'))}</td>"
                f"<td>{_fmt(stats.get('accuracy'), pct=True)}</td><td>{_fmt(stats.get('brier_score'))}</td></tr>"
            )

    plots_html = ""
    if runs_metrics.get("plots"):
        for label, path in runs_metrics["plots"].items():
            fname = os.path.basename(path)
            plots_html += f"<h3>{label}</h3><img src='{fname}' alt='{label}'>"

    conclusions_html = "".join(f"<li>{c}</li>" for c in conclusions)

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Historical Report {season_year}</title>{_STYLE}</head>
<body><div class="page">
<div class="cover">
  <h1>Historical Backtesting Report — temporada {season_year}</h1>
  <p>Generado {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · run_id={run_id} ·
     historical_engine (aislado de producción)</p>
</div>
<div class="content">

<div class="callout-warning">⚠ {BULLPEN_RISK_WARNING}</div>

<h2>Comparación entre modelos</h2>
<table><thead><tr><th>Motor</th><th>n</th><th>Accuracy</th><th>Brier</th><th>Log-loss</th><th>ECE</th><th>MCE</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<div class="callout">Ningún modelo se marca como "ganador" automáticamente -- ver conclusiones abajo para el contexto de cada número.</div>

<h2>Drift entre temporadas</h2>
{"<table><thead><tr><th>Motor</th><th>Temporada</th><th>n</th><th>Accuracy</th><th>Brier</th></tr></thead><tbody>" + ''.join(drift_rows) + "</tbody></table>" if drift_rows else "<p><span class='na'>N/D</span> -- no se pasaron temporadas adicionales para comparar drift.</p>"}

<h2>Diferencia de carreras (proyectado vs. real)</h2>
<p>n={runs_metrics.get('n', 0)} · MAE total={_fmt(runs_metrics.get('total_mae'))} ·
   RMSE total={_fmt(runs_metrics.get('total_rmse'))} · Bias total={_fmt(runs_metrics.get('total_bias'))} ·
   R²={_fmt(runs_metrics.get('total_r2'))} · Pearson={_fmt(runs_metrics.get('total_pearson'))} ·
   Spearman={_fmt(runs_metrics.get('total_spearman'))}</p>
{plots_html}

<h2>Conclusiones automáticas (basadas únicamente en los números de arriba)</h2>
<ul>{conclusions_html}</ul>

</div>
<footer>historical_engine -- aislado de producción. Fuente: historical_engine.db, nunca mlb_edge.db.</footer>
</div></body></html>"""
