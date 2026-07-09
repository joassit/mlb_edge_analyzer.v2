"""
TEMPORAL -- corrida real de UN MES (2024-05) pedida como punto 4 por el
usuario, antes de decidir si escalar a temporada completa. Se corre UNA vez
vía GitHub Actions (unico entorno con salida de red a statsapi.mlb.com
disponible) y se retira junto con el workflow que lo invoca al terminar.
No es parte permanente de historical_engine -- usa historical_engine.db
(su propia base, separada de mlb_edge.db) tal como ya esta diseñado.
"""

import sys
import time

from historical_engine import pipeline
from historical_engine.db import init_historical_db
from historical_engine.validation import validate_all_sources
from historical_engine.training import propose_dispersion_recalibration
from historical_engine.runs_analysis import analyze_runs_projection

SEASON = 2024
MONTH = 5


def main() -> int:
    init_historical_db()

    print("=" * 100)
    print(f"PUNTO 4 -- CORRIDA REAL DE UN MES ({SEASON}-{MONTH:02d}) -- historical_engine.db, aislado de produccion")
    print("=" * 100)

    t0 = time.monotonic()
    result = pipeline.run_month(SEASON, MONTH)
    elapsed = time.monotonic() - t0

    print(f"\nrun_id={result.run_id}")
    print(f"juegos en el calendario del mes: {result.n_games}")
    print(f"juegos analizados (con abridores confirmados + bullpen resuelto): {result.n_analyzed}")
    print(f"juegos saltados (sin abridor confirmado o sin datos point-in-time suficientes): {result.n_skipped_missing_pitcher}")
    print(f"errores: {result.n_errors}")
    for e in result.errors[:15]:
        print(f"  - {e}")
    print(f"\nTIEMPO TOTAL DE INGESTA + ANALISIS: {elapsed:.1f}s ({elapsed/60:.1f} min) para {result.n_games} juegos")
    if result.n_games > 0:
        per_game = elapsed / result.n_games
        print(f"tiempo promedio por juego: {per_game:.2f}s")
        full_season_estimate_s = per_game * 2430
        print(f"proyeccion extrapolada a temporada completa (~2430 juegos, sin cache): "
              f"~{full_season_estimate_s/60:.0f} min (~{full_season_estimate_s/3600:.1f} h)")

    print("\n--- VALIDACION (accuracy/Brier/ECE por motor, calibracion por bucket de confianza) ---")
    validation_results = validate_all_sources(SEASON, result.run_id)
    for source, metrics in validation_results.items():
        print(f"{source}: n={metrics['n_sample']} accuracy={metrics['accuracy']} "
              f"brier={metrics['brier_score']} log_loss={metrics['log_loss']} "
              f"ece={metrics['ece']} mce={metrics['mce']} sharpness={metrics['sharpness']} "
              f"accuracy_ci_bootstrap={metrics['accuracy_ci']}")

    print("\n--- ENTRENAMIENTO -- candidatos de k (NEGBIN_DISPERSION) contra el mes real ---")
    train_result = propose_dispersion_recalibration(SEASON, result.run_id)
    print(f"Baseline NEGBIN_DISPERSION={train_result['baseline_value']} "
          f"brier={train_result['baseline_brier_score']} n={train_result['baseline_n_sample']}")
    for p in train_result["proposals"]:
        print(f"  candidato k={p['param_value']}: brier={p['brier_score']} n={p['n_sample']} "
              f"mejora_sobre_baseline={p['improved_over_baseline']}")
    print(f"mejor candidato: {train_result['best_candidate']}")
    print(train_result["note"])

    print("\n--- MAE/RMSE del margen de carreras proyectado vs. real (el mes completo) ---")
    runs_metrics = analyze_runs_projection(result.run_id, output_dir="/tmp/tmp_historical_month_plots")
    if runs_metrics.get("n", 0) == 0:
        print(f"::warning::Sin juegos con proyeccion y resultado real -- {runs_metrics.get('warning')}")
    else:
        print(f"n={runs_metrics['n']}")
        print(f"TOTAL (home+away): MAE={runs_metrics['total_mae']:.3f} RMSE={runs_metrics['total_rmse']:.3f} "
              f"bias={runs_metrics['total_bias']:.3f} R2={runs_metrics['total_r2']} "
              f"pearson={runs_metrics['total_pearson']} spearman={runs_metrics['total_spearman']}")
        print(f"HOME: MAE={runs_metrics['home_mae']:.3f} RMSE={runs_metrics['home_rmse']:.3f} bias={runs_metrics['home_bias']:.3f}")
        print(f"AWAY: MAE={runs_metrics['away_mae']:.3f} RMSE={runs_metrics['away_rmse']:.3f} bias={runs_metrics['away_bias']:.3f}")
        print(f"desglose: {runs_metrics['breakdown']}")

    print("\n" + "=" * 100)
    print("FIN PUNTO 4")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
