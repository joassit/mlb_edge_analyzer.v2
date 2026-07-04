"""
Tracking de resultados reales — el cimiento antes de cualquier ML.

Dos responsabilidades:
1. update_results(): busca predicciones de días pasados sin resultado
   guardado, y trae el marcador final real vía MLB Stats API.
2. compute_metrics(): calcula accuracy y Brier Score comparando predicción
   vs. resultado real — la señal objetiva de si el modelo sirve o no.

Brier Score: promedio de (probabilidad_predicha - resultado_real)^2.
0.0 = predicciones perfectas. 0.25 = equivalente a "siempre digo 50%".
Más de 0.25 significa que el modelo está peor que no decir nada.
"""

import logging
import math

from data.mlb_api import get_game_result
from db.database import (
    SessionLocal, GameAnalysis, ActualResult, Bet,
    save_result, get_predictions_without_result, settle_bets_for_game,
)

logger = logging.getLogger("mlb_edge_analyzer")


def update_results(days_back: int = 5) -> int:
    """
    Busca resultados reales para predicciones pasadas que aún no los
    tienen. Devuelve cuántos juegos se actualizaron.
    """
    pending = get_predictions_without_result(days_back=days_back)
    updated = 0

    for pred in pending:
        try:
            result = get_game_result(pred["game_pk"])
        except Exception as e:
            logger.warning(f"No se pudo consultar resultado de game_pk={pred['game_pk']}: {e}")
            continue

        if result is None:
            continue  # el juego todavía no termina, o se pospuso

        save_result({
            "game_pk": pred["game_pk"],
            "game_date": pred["game_date"],
            "home_score": result["home_score"],
            "away_score": result["away_score"],
            "winner": result["winner"],
            "total_runs": result["total_runs"],
        })
        updated += 1

        settled = settle_bets_for_game(pred["game_pk"], result["winner"])
        if settled:
            logger.info(f"{settled} apuesta(s) liquidada(s) para game_pk={pred['game_pk']}")

        logger.info(
            f"Resultado guardado: {pred['away_team']} @ {pred['home_team']} "
            f"-> {result['away_score']}-{result['home_score']} ({result['winner']} gana)"
        )

    return updated


def compute_metrics(days: int = 30) -> dict:
    """
    Calcula accuracy y Brier Score sobre las predicciones de los últimos
    N días que ya tienen resultado real registrado.
    """
    from datetime import date, timedelta

    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

    session = SessionLocal()
    try:
        rows = (
            session.query(GameAnalysis, ActualResult)
            .join(ActualResult, GameAnalysis.game_pk == ActualResult.game_pk)
            .filter(GameAnalysis.game_date >= cutoff)
            .all()
        )
    finally:
        session.close()

    if not rows:
        return {"n_games": 0, "accuracy": None, "brier_score": None}

    correct = 0
    brier_sum = 0.0
    log_loss_sum = 0.0

    for pred, result in rows:
        actual_home_win = 1 if result.winner == "home" else 0
        predicted_winner_is_home = pred.home_model_prob > 0.5
        actual_winner_is_home = actual_home_win == 1

        if predicted_winner_is_home == actual_winner_is_home:
            correct += 1

        brier_sum += (pred.home_model_prob - actual_home_win) ** 2

        p_clipped = min(max(pred.home_model_prob, 1e-15), 1 - 1e-15)
        log_loss_sum += -(
            actual_home_win * math.log(p_clipped) +
            (1 - actual_home_win) * math.log(1 - p_clipped)
        )

    n = len(rows)
    return {
        "n_games": n,
        "accuracy": correct / n,
        "brier_score": brier_sum / n,
        "log_loss": log_loss_sum / n,
    }


def compute_bet_performance(days: int = 30) -> dict:
    """
    Desempeño de las apuestas REALES que registraste (no solo predicciones).
    Esto es lo que de verdad importa: ganar dinero, no solo acertar juegos.
    """
    from datetime import date, timedelta

    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

    session = SessionLocal()
    try:
        settled = (
            session.query(Bet)
            .filter(Bet.game_date >= cutoff, Bet.result.in_(["win", "loss"]))
            .all()
        )
    finally:
        session.close()

    if not settled:
        return {"n_bets": 0, "total_staked": None, "total_profit": None, "roi": None}

    total_staked = sum(b.stake for b in settled)
    total_profit = sum(b.profit for b in settled)
    wins = sum(1 for b in settled if b.result == "win")

    return {
        "n_bets": len(settled),
        "wins": wins,
        "win_rate": wins / len(settled),
        "total_staked": total_staked,
        "total_profit": total_profit,
        "roi": (total_profit / total_staked) if total_staked > 0 else None,
    }


def print_performance_report(days: int = 30) -> None:
    metrics = compute_metrics(days=days)

    print(f"\n{'=' * 60}")
    print(f"  DESEMPEÑO DEL MODELO — últimos {days} días")
    print(f"{'=' * 60}\n")

    if metrics["n_games"] == 0:
        print("Todavía no hay suficientes resultados registrados.")
        print("Corre este script varios días seguidos para acumular datos.\n")
        return

    print(f"Juegos evaluados:  {metrics['n_games']}")
    print(f"Accuracy:          {metrics['accuracy']:.1%}  (favorito correcto)")
    print(f"Brier Score:       {metrics['brier_score']:.4f}  "
          f"(0.0=perfecto, 0.25=equivalente a adivinar 50%, >0.25=peor que adivinar)")
    print(f"Log Loss:          {metrics['log_loss']:.4f}  "
          f"(penaliza más fuerte estar muy seguro y equivocado)")

    bet_perf = compute_bet_performance(days=days)
    print()
    if bet_perf["n_bets"] == 0:
        print("Todavía no hay apuestas reales registradas (esto es normal si solo")
        print("estás observando predicciones sin apostar todavía).")
    else:
        print(f"--- Apuestas reales registradas ---")
        print(f"Apuestas liquidadas: {bet_perf['n_bets']}  ({bet_perf['wins']} ganadas, "
              f"{bet_perf['win_rate']:.1%} win rate)")
        print(f"Total apostado:      {bet_perf['total_staked']:.2f} unidades")
        print(f"Profit/pérdida:      {bet_perf['total_profit']:+.2f} unidades")
        print(f"ROI:                 {bet_perf['roi']:+.1%}")
    print()
