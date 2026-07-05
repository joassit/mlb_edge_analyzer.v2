"""
Backtest walk-forward: recalcula predicciones desde los snapshots
congelados en FeatureSnapshot (nunca vuelve a golpear ninguna API) y mide
Brier Score juego por juego, ordenado por fecha.

Por qué esto no filtra información del futuro: cada FeatureSnapshot
contiene exactamente los insumos crudos (ERA, OPS, bullpen, parque, clima,
cuota) que main.py vio el día en que corrió esa predicción — a diferencia
de volver a llamar data/stats.py hoy, que traería estadísticas de temporada
acumuladas A HOY, no a la fecha del juego.

Con poca data acumulada esto no es todavía un backtest estadísticamente
significativo — se degrada explícitamente (`message`) en vez de aparentar
una validación que no existe.
"""

import json

from db.database import SessionLocal, FeatureSnapshot, ActualResult
from model.predictor import predict_from_raw_inputs


def walk_forward_backtest(min_games: int = 10) -> dict:
    session = SessionLocal()
    try:
        rows = (
            session.query(FeatureSnapshot, ActualResult)
            .join(ActualResult, FeatureSnapshot.game_pk == ActualResult.game_pk)
            .order_by(FeatureSnapshot.game_date)
            .all()
        )
    finally:
        session.close()

    if len(rows) < min_games:
        return {
            "n_games": len(rows),
            "brier_score": None,
            "per_game": [],
            "message": (
                f"Se necesitan al menos {min_games} juegos con snapshot + resultado "
                f"real para que el backtest diga algo; hay {len(rows)} disponibles."
            ),
        }

    brier_sum = 0.0
    per_game = []
    for snap, result in rows:
        raw_inputs = json.loads(snap.raw_inputs_json)
        prediction = predict_from_raw_inputs(raw_inputs)
        actual_home_win = 1 if result.winner == "home" else 0
        sq_error = (prediction["home_model_prob"] - actual_home_win) ** 2
        brier_sum += sq_error
        per_game.append({
            "game_pk": snap.game_pk,
            "game_date": snap.game_date,
            "model_version_used": snap.model_version,
            "brier": sq_error,
        })

    return {
        "n_games": len(rows),
        "brier_score": brier_sum / len(rows),
        "per_game": per_game,
        "message": None,
    }


def print_backtest_report(min_games: int = 10) -> None:
    result = walk_forward_backtest(min_games=min_games)

    print(f"\n{'=' * 60}")
    print("  BACKTEST WALK-FORWARD (sobre snapshots congelados)")
    print(f"{'=' * 60}\n")

    if result["message"]:
        print(result["message"])
        return

    print(f"Juegos evaluados: {result['n_games']}")
    print(f"Brier Score:      {result['brier_score']:.4f}")
    print()


if __name__ == "__main__":
    print_backtest_report()
