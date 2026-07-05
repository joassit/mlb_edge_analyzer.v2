"""
Genera el reporte diario en consola y opcionalmente lo exporta a CSV.
"""

from datetime import date
import csv
import os


def print_report(rows: list[dict]) -> None:
    if not rows:
        print("No hay juegos analizados hoy.")
        return

    print("\n" + "=" * 70)
    print(f"  MLB EDGE ANALYZER — Reporte del {date.today().strftime('%Y-%m-%d')}")
    print("=" * 70 + "\n")

    for r in rows:
        print(f"{r['away_team']} @ {r['home_team']}")
        print(f"  Pitchers: {r['away_pitcher'] or 'TBD'}  vs  {r['home_pitcher'] or 'TBD'}")
        if r.get("away_bullpen_era") is not None:
            print(f"  Bullpen  -> visitante: {r['away_bullpen_era']:.2f}   local: {r['home_bullpen_era']:.2f}")
        if r.get("away_k_pct") is not None:
            print(f"  K%       -> visitante: {r['away_k_pct']:.1%}   local: {r['home_k_pct']:.1%}")
        if r.get("away_days_rest") is not None:
            print(f"  Descanso -> visitante: {r['away_days_rest']}d   local: {r['home_days_rest']}d")
        if r.get("park_name"):
            temp_txt = f", {r['temp_f']:.0f}°F" if r.get("temp_f") is not None else ""
            print(f"  Parque   -> {r['park_name']} (factor {r['park_factor']:.2f}{temp_txt})")
        print(f"  Modelo   -> visitante: {r['away_model_prob']:.3f}   local: {r['home_model_prob']:.3f}")

        if r.get("away_skellam_prob") is not None:
            print(f"  Skellam  -> visitante: {r['away_skellam_prob']:.3f}   local: {r['home_skellam_prob']:.3f}"
                  f"   (carreras proy.: {r['away_proj_runs']:.1f} - {r['home_proj_runs']:.1f})")

            fav_a = r["away_model_prob"] > 0.5
            fav_b = r["away_skellam_prob"] > 0.5
            agree = "✅ ambos modelos coinciden en el favorito" if fav_a == fav_b else "⚠️  los modelos DISCREPAN en el favorito"
            print(f"  {agree}")

        if r.get("home_covers_rl_prob") is not None:
            print(f"  Run Line -> {r['home_team']} -1.5: {r['home_covers_rl_prob']:.1%}   "
                  f"{r['away_team']} +1.5: {r['away_covers_rl_prob']:.1%}")
        if r.get("fair_total_runs") is not None:
            print(f"  Total    -> línea justa del modelo: {r['fair_total_runs']:.1f} carreras "
                  f"(compárala contra la línea real de tu casa de apuestas)")
        if r.get("away_market_prob") is not None:
            print(f"  Mercado  -> visitante: {r['away_market_prob']:.3f}   local: {r['home_market_prob']:.3f}"
                  f"  (implícita, con vig)")
            if r.get("away_market_no_vig_prob") is not None:
                print(f"  Sin vig  -> visitante: {r['away_market_no_vig_prob']:.3f}   "
                      f"local: {r['home_market_no_vig_prob']:.3f}  (consenso, sin margen de casa)")

            if r.get("market_favorite_team"):
                print(f"  Favorito del mercado -> {r['market_favorite_team']} ({r['market_favorite_prob']:.1%})")
            elif r.get("market_favorite_prob") is not None:
                print(f"  Favorito del mercado -> pick'em, sin favorito claro (~{r['market_favorite_prob']:.1%})")

            print(f"  Edge     -> visitante: {r['away_edge']:+.3f}   local: {r['home_edge']:+.3f}")
            print(f"  EV       -> visitante: {r['away_ev']:+.3f}   local: {r['home_ev']:+.3f}  (por unidad apostada)")

            if r.get("flag_review"):
                print(f"  🔎 candidato a revisión: edge >= umbral y los dos modelos coinciden en el favorito")
        else:
            print("  Mercado  -> (sin cuotas cargadas todavía)")
        print("-" * 70)


def export_csv(rows: list[dict], path: str = None) -> str:
    if path is None:
        path = f"reports/reporte_{date.today().strftime('%Y%m%d')}.csv"

    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None

    if not rows:
        return path

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    return path