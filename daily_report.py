"""
DAILY REPORT — el reporte del día completo, en tres partes:

1. JUGADOS: compara la predicción que se hizo esta mañana (cuando el juego
   aún era "Preview") contra el resultado real. Esto es la auditoría.
2. EN PROGRESO: solo se marcan, no se evalúan (el resultado no es final).
3. POR JUGAR: diagnóstico normal (igual que main.py) para los que faltan.

Requiere que hayas corrido `python main.py` en algún momento del día,
mientras esos juegos todavía estaban en "Preview" — si no, no hay
predicción guardada para auditar.

Uso:
    python daily_report.py
"""

from datetime import date

from logging_config import setup_logging
from data.mlb_api import get_schedule, get_game_result
from data.weather import preload_weather
from data.park_factors import get_park_info
from data.stats import get_league_ops
from db.database import init_db, get_prediction_by_game_pk, save_result, settle_bets_for_game
from main import build_prediction_row


def audit_finished_game(g: dict) -> None:
    """Compara predicción guardada vs. resultado real de un juego ya terminado."""
    today_str = date.today().strftime("%Y-%m-%d")
    pred = get_prediction_by_game_pk(g["game_pk"], today_str)

    print(f"{g['away_team']} @ {g['home_team']}  [JUGADO]")

    if pred is None:
        print("  ⚠️  No había predicción guardada de este juego "
              "(¿corriste main.py antes de que empezara?)")
        print("-" * 70)
        return

    result = get_game_result(g["game_pk"])
    if result is None:
        print("  ⚠️  El resultado real todavía no está disponible en la API")
        print("-" * 70)
        return

    # TODO: Defensa contra pitcher scratch — get_actual_starters no implementada aún

    home_won = result["winner"] == "home"
    predicted_home_favorite = pred["home_model_prob"] > 0.5
    correct = predicted_home_favorite == home_won

    brier = (pred["home_model_prob"] - (1 if home_won else 0)) ** 2

    print(f"  Resultado real: {result['away_score']}-{result['home_score']} "
          f"({'local' if home_won else 'visitante'} ganó)")
    print(f"  Predicción     -> visitante: {pred['away_model_prob']:.3f}   local: {pred['home_model_prob']:.3f}")
    if pred.get("away_skellam_prob") is not None:
        print(f"  Skellam        -> visitante: {pred['away_skellam_prob']:.3f}   local: {pred['home_skellam_prob']:.3f}")
    if pred.get("fair_total_runs") is not None:
        print(f"  Total proyectado: {pred['fair_total_runs']:.1f}   Total real: {result['total_runs']}")
    print(f"  {'✅ ACERTÓ el favorito' if correct else '❌ FALLÓ el favorito'}   (Brier: {brier:.3f})")

    # Guarda el resultado y liquida cualquier apuesta pendiente de este juego
    save_result({
        "game_pk": g["game_pk"], "game_date": today_str,
        "home_score": result["home_score"], "away_score": result["away_score"],
        "winner": result["winner"], "total_runs": result["total_runs"],
    })
    settle_bets_for_game(g["game_pk"], result["winner"])

    print("-" * 70)


def flag_in_progress_game(g: dict) -> None:
    print(f"{g['away_team']} @ {g['home_team']}  [EN PROGRESO — sin evaluar todavía]")
    print("-" * 70)


def diagnose_upcoming_game(row: dict) -> None:
    print(f"{row['away_team']} @ {row['home_team']}  [POR JUGAR]")
    print(f"  Pitchers: {row['away_pitcher'] or 'TBD'}  vs  {row['home_pitcher'] or 'TBD'}")
    print(f"  Modelo   -> visitante: {row['away_model_prob']:.3f}   local: {row['home_model_prob']:.3f}")
    print(f"  Skellam  -> visitante: {row['away_skellam_prob']:.3f}   local: {row['home_skellam_prob']:.3f}"
          f"   (carreras proy.: {row['away_proj_runs']:.1f} - {row['home_proj_runs']:.1f})")
    print(f"  Total    -> línea justa del modelo: {row['fair_total_runs']:.1f} carreras")
    print("-" * 70)


def run_daily_report() -> None:
    today = date.today()
    games = get_schedule(today)

    jugados = [g for g in games if g.get("abstract_state") == "Final"]
    en_progreso = [g for g in games if g.get("abstract_state") == "Live"]
    por_jugar = [g for g in games if g.get("abstract_state") == "Preview"]

    print("\n" + "=" * 70)
    print(f"  REPORTE DEL DÍA — {today.strftime('%Y-%m-%d')}")
    print(f"  Jugados: {len(jugados)}   En progreso: {len(en_progreso)}   Por jugar: {len(por_jugar)}")
    print("=" * 70 + "\n")

    if jugados:
        print("── AUDITORÍA DE JUEGOS TERMINADOS " + "─" * 35 + "\n")
        aciertos = 0
        evaluados = 0
        for g in jugados:
            before = get_prediction_by_game_pk(g["game_pk"], today.strftime("%Y-%m-%d"))
            audit_finished_game(g)
            if before is not None:
                evaluados += 1
        if evaluados:
            print(f"(Auditoría completa arriba — corre 'python track_results.py' "
                  f"para ver el Brier Score acumulado histórico)\n")

    if en_progreso:
        print("── EN PROGRESO " + "─" * 54 + "\n")
        for g in en_progreso:
            flag_in_progress_game(g)
        print()

    if por_jugar:
        print("── DIAGNÓSTICO DE LOS QUE FALTAN " + "─" * 36 + "\n")
        league_ops = get_league_ops()
        weather_by_team = preload_weather(por_jugar, get_park_info)
        for g in por_jugar:
            row = build_prediction_row(g, league_ops, weather_by_team)
            if row:
                diagnose_upcoming_game(row)
            else:
                print(f"{g['away_team']} @ {g['home_team']}  [sin datos suficientes todavía]")
                print("-" * 70)


if __name__ == "__main__":
    logger = setup_logging()
    init_db()
    run_daily_report()
