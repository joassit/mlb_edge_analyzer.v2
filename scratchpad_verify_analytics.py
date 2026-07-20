"""
Verificación de un solo uso contra el Postgres real de producción: corre
las 4 funciones nuevas (compute_edge_lift, compute_pick_performance_by_probability,
compute_rolling_metrics, decompose_game_projection) y el gráfico
(plot_rolling_metrics) sobre los datos reales acumulados, para confirmar
que no hay sorpresas de forma/tipo de dato que las pruebas sintéticas no
puedan ver.
"""
import json
import sys

sys.path.insert(0, ".")

from db.database import SessionLocal, GameAnalysis, Pick
from tracking.results_tracker import (
    compute_edge_lift, compute_pick_performance_by_probability,
    compute_rolling_metrics, decompose_game_projection,
)
from reports.rolling_charts import plot_rolling_metrics

print("=== compute_edge_lift(days=30) ===")
print(json.dumps(compute_edge_lift(days=30), indent=1, default=str))

print("\n=== compute_pick_performance_by_probability(days=30) ===")
print(json.dumps(compute_pick_performance_by_probability(days=30), indent=1, default=str))

print("\n=== compute_rolling_metrics(window=30) ===")
rolling = compute_rolling_metrics(window=30)
print(f"n_games_available={rolling['n_games_available']} n_real_picks_available={rolling['n_real_picks_available']}")
print(f"len(brier_series)={len(rolling['brier_series'])} len(roi_series)={len(rolling['roi_series'])}")
if rolling["brier_series"]:
    print("primer punto:", rolling["brier_series"][0])
    print("ultimo punto:", rolling["brier_series"][-1])
if rolling["roi_series"]:
    print("primer punto ROI:", rolling["roi_series"][0])
    print("ultimo punto ROI:", rolling["roi_series"][-1])

chart_result = plot_rolling_metrics(rolling, ".", filename="rolling_verify.png")
print("chart:", chart_result)

print("\n=== decompose_game_projection() sobre un juego real de hoy ===")
session = SessionLocal()
try:
    today_pick = (
        session.query(Pick)
        .filter(Pick.game_date == "2026-07-20", Pick.market == "moneyline")
        .first()
    )
finally:
    session.close()

if today_pick:
    decomposition = decompose_game_projection(today_pick.game_pk, today_pick.game_date)
    print(f"game_pk={today_pick.game_pk}")
    print(json.dumps(decomposition, indent=1, default=str))
else:
    print("Sin picks de hoy encontrados -- N/A")

print("VERIFY_ANALYTICS_OK")
