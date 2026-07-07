"""
Backtesting histórico: ¿el modelo realmente tiene edge, o no?

Compara cada predicción ya guardada en game_analysis contra el resultado
real (actual_results, poblada por results_fetcher.py) y calcula:

  - Accuracy del favorito del modelo (todas las predicciones con resultado)
  - Win rate / ROI simulado apostando $100 flat a cada "pick sugerido"
    (el lado con edge positivo vs. la cuota de mercado registrada)
  - Lo mismo pero solo para picks con edge fuerte (> edge_threshold)
  - Calibración: probabilidad promedio predicha vs. frecuencia real de
    acierto, por bucket de confianza

Uso:
    python backtest.py [--edge-threshold 0.03] [--min-sample 30]
    o bien: from backtest import evaluate_model
"""

import argparse
import csv
import sqlite3
from datetime import date

import pandas as pd

from model.edge import fair_odds

STAKE = 100.0
DEFAULT_EDGE_THRESHOLD = 0.03
DEFAULT_MIN_SAMPLE = 30


def _load_joined(db_path: str) -> pd.DataFrame:
    con = sqlite3.connect(db_path)
    try:
        predictions = pd.read_sql_query("SELECT * FROM game_analysis", con)
        results = pd.read_sql_query(
            "SELECT game_pk, home_score, away_score, winner, total_runs FROM actual_results", con
        )
    finally:
        con.close()

    if predictions.empty or results.empty:
        return pd.DataFrame()

    # Defensa contra duplicados históricos (mismo game_pk guardado más de
    # una vez antes de que save_analysis fuera idempotente): solo la más
    # reciente por juego.
    predictions = (
        predictions.sort_values("created_at")
        .drop_duplicates(subset="game_pk", keep="last")
    )

    return predictions.merge(results, on="game_pk", how="inner")


def _payout_multiplier(odds: float) -> float:
    """Ganancia neta por unidad apostada si gana, dada una cuota americana."""
    return (100 / abs(odds)) if odds < 0 else (odds / 100)


def _simulate_bets(rows: pd.DataFrame) -> dict:
    """Simula apostar STAKE flat a cada pick sugerido (lado con edge > 0)."""
    if rows.empty:
        return {"n_picks": 0, "wins": 0, "win_rate": None,
                "total_staked": 0.0, "total_profit": 0.0, "roi": None}

    wins = 0
    total_profit = 0.0
    for _, r in rows.iterrows():
        pick_side = "home" if r["pick_edge"] == r["home_edge"] else "away"
        picked_market_prob = r["home_market_prob"] if pick_side == "home" else r["away_market_prob"]
        odds = fair_odds(picked_market_prob)
        b = _payout_multiplier(odds)
        won = r["winner"] == pick_side
        if won:
            wins += 1
            total_profit += STAKE * b
        else:
            total_profit -= STAKE

    n = len(rows)
    total_staked = STAKE * n
    return {
        "n_picks": n,
        "wins": wins,
        "win_rate": wins / n,
        "total_staked": total_staked,
        "total_profit": total_profit,
        "roi": total_profit / total_staked,
    }


def _calibration_buckets(df: pd.DataFrame, bucket_size: float = 0.05) -> list[dict]:
    """
    Bucketiza la confianza del modelo en el favorito (siempre >= 50%) y
    compara la probabilidad promedio predicha vs. la frecuencia real de
    acierto en cada bucket.
    """
    pick_prob = df[["home_model_prob", "away_model_prob"]].max(axis=1)
    pick_is_home = df["home_model_prob"] > df["away_model_prob"]
    actual_is_home = df["winner"] == "home"
    correct = pick_is_home == actual_is_home

    buckets = []
    n_buckets = int(round(0.5 / bucket_size))
    for i in range(n_buckets):
        lo = 0.5 + i * bucket_size
        hi = lo + bucket_size
        in_bucket = (pick_prob >= lo) & (pick_prob < hi if i < n_buckets - 1 else pick_prob <= hi)
        n = int(in_bucket.sum())
        if n == 0:
            continue
        buckets.append({
            "bucket": f"{lo:.0%}-{hi:.0%}",
            "n": n,
            "avg_predicted_prob": round(float(pick_prob[in_bucket].mean()), 4),
            "actual_hit_rate": round(float(correct[in_bucket].mean()), 4),
        })
    return buckets


def evaluate_model(db_path: str = "mlb_edge.db",
                    edge_threshold: float = DEFAULT_EDGE_THRESHOLD,
                    min_sample_size: int = DEFAULT_MIN_SAMPLE) -> dict:
    """Evalúa todo el historial con resultado ya conocido. Ver docstring del módulo."""
    df = _load_joined(db_path)

    if df.empty:
        return {
            "n_games": 0,
            "small_sample_warning": "Todavía no hay predicciones con resultado real registrado. "
                                     "Corre results_fetcher.py después de que los juegos terminen.",
            "overall_favorite_accuracy": None,
            "betting": _simulate_bets(pd.DataFrame()),
            "betting_strong_edge": {"threshold": edge_threshold, **_simulate_bets(pd.DataFrame())},
            "calibration": [],
        }

    n_games = len(df)

    pick_is_home = df["home_model_prob"] > df["away_model_prob"]
    actual_is_home = df["winner"] == "home"
    overall_accuracy = float((pick_is_home == actual_is_home).mean())

    with_odds = df[df["away_market_prob"].notna() & df["home_market_prob"].notna()].copy()
    if not with_odds.empty:
        with_odds["pick_edge"] = with_odds[["home_edge", "away_edge"]].max(axis=1)
        picks = with_odds[with_odds["pick_edge"] > 0]
    else:
        picks = with_odds

    betting = _simulate_bets(picks)

    strong_picks = picks[picks["pick_edge"] > edge_threshold] if not picks.empty else picks
    betting_strong = {"threshold": edge_threshold, **_simulate_bets(strong_picks)}

    calibration = _calibration_buckets(df)

    warning = None
    if n_games < min_sample_size:
        warning = (f"Muestra pequeña (n={n_games}): resultados no son "
                   f"estadísticamente concluyentes todavía.")

    return {
        "n_games": n_games,
        "small_sample_warning": warning,
        "overall_favorite_accuracy": round(overall_accuracy, 4),
        "betting": betting,
        "betting_strong_edge": betting_strong,
        "calibration": calibration,
    }


def print_backtest_report(metrics: dict) -> None:
    print("\n" + "=" * 70)
    print("  BACKTEST — desempeño histórico del modelo")
    print("=" * 70 + "\n")

    if metrics["n_games"] == 0:
        print(metrics["small_sample_warning"])
        return

    print(f"Juegos evaluados (con resultado real): {metrics['n_games']}")
    print(f"Accuracy del favorito del modelo:      {metrics['overall_favorite_accuracy']:.1%}")

    b = metrics["betting"]
    print()
    if b["n_picks"] == 0:
        print("Sin picks con edge positivo y cuota registrada todavía (no hay ROI que simular).")
    else:
        print(f"--- Picks sugeridos (edge > 0, ${STAKE:.0f} flat c/u) ---")
        print(f"Win rate: {b['win_rate']:.1%} sobre {b['n_picks']} picks | "
              f"ROI: {b['roi']:+.1%} (${b['total_profit']:+.2f} sobre ${b['total_staked']:.2f} apostados)")

    bs = metrics["betting_strong_edge"]
    print()
    if bs["n_picks"] == 0:
        print(f"Sin picks con edge > {bs['threshold']:.0%} todavía.")
    else:
        print(f"--- Solo picks con edge > {bs['threshold']:.0%} ---")
        print(f"Win rate: {bs['win_rate']:.1%} sobre {bs['n_picks']} picks | ROI: {bs['roi']:+.1%}")

    print()
    if metrics["calibration"]:
        print("--- Calibración (confianza del modelo vs. acierto real) ---")
        for row in metrics["calibration"]:
            print(f"  {row['bucket']:<10} n={row['n']:<4} "
                  f"predicho={row['avg_predicted_prob']:.1%}  real={row['actual_hit_rate']:.1%}")

    if metrics["small_sample_warning"]:
        print(f"\n[!] {metrics['small_sample_warning']}")
    print()


def export_backtest_csv(metrics: dict, path: str = None) -> str:
    if path is None:
        path = f"reports/backtest_{date.today().strftime('%Y%m%d')}.csv"

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "value"])
        writer.writerow(["n_games", metrics["n_games"]])
        writer.writerow(["overall_favorite_accuracy", metrics["overall_favorite_accuracy"]])

        b = metrics["betting"]
        writer.writerow(["betting_n_picks", b["n_picks"]])
        writer.writerow(["betting_win_rate", b["win_rate"]])
        writer.writerow(["betting_total_staked", b["total_staked"]])
        writer.writerow(["betting_total_profit", b["total_profit"]])
        writer.writerow(["betting_roi", b["roi"]])

        bs = metrics["betting_strong_edge"]
        writer.writerow(["strong_edge_threshold", bs["threshold"]])
        writer.writerow(["strong_edge_n_picks", bs["n_picks"]])
        writer.writerow(["strong_edge_win_rate", bs["win_rate"]])
        writer.writerow(["strong_edge_roi", bs["roi"]])

        writer.writerow(["small_sample_warning", metrics["small_sample_warning"] or ""])

        writer.writerow([])
        writer.writerow(["calibration_bucket", "n", "avg_predicted_prob", "actual_hit_rate"])
        for row in metrics["calibration"]:
            writer.writerow([row["bucket"], row["n"], row["avg_predicted_prob"], row["actual_hit_rate"]])

    return path


def main():
    parser = argparse.ArgumentParser(description="Backtest del modelo contra resultados reales")
    parser.add_argument("--edge-threshold", type=float, default=DEFAULT_EDGE_THRESHOLD)
    parser.add_argument("--min-sample", type=int, default=DEFAULT_MIN_SAMPLE)
    args = parser.parse_args()

    metrics = evaluate_model(edge_threshold=args.edge_threshold, min_sample_size=args.min_sample)
    print_backtest_report(metrics)

    if metrics["n_games"] > 0:
        path = export_backtest_csv(metrics)
        print(f"Backtest exportado a: {path}")


if __name__ == "__main__":
    main()
