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
    SessionLocal, GameAnalysis, ActualResult, Bet, Pick,
    save_result, get_predictions_without_result, settle_bets_for_game, settle_picks_for_game,
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

        settled_picks = settle_picks_for_game(pred["game_pk"], result)
        if settled_picks:
            logger.info(f"{settled_picks} pick(s) liquidado(s) para game_pk={pred['game_pk']}")

        logger.info(
            f"Resultado guardado: {pred['away_team']} @ {pred['home_team']} "
            f"-> {result['away_score']}-{result['home_score']} ({result['winner']} gana)"
        )

    return updated


def validate_probabilities(pred: GameAnalysis) -> bool:
    """
    Sanity check de rango [0,1] y de que las probabilidades del mismo
    modelo sumen ~1 -- protege compute_metrics() de filas corruptas (bug de
    persistencia, migración incompleta, edición manual de la DB) que de
    otro modo inflarían o hundirían el Brier Score/accuracy en silencio en
    vez de saltar a la vista como un dato malo.
    """
    errors = []

    for field in ("away_model_prob", "home_model_prob", "away_skellam_prob", "home_skellam_prob"):
        p = getattr(pred, field)
        if p is None:
            continue
        if not (0.0 <= p <= 1.0):
            errors.append(f"{field}={p} fuera de rango [0,1]")

    # compute_metrics() solo depende de home_model_prob -- away_model_prob
    # puede faltar en filas parciales/antiguas, así que el chequeo de suma
    # se omite (no se rechaza la fila) cuando no hay con qué compararla.
    if pred.away_model_prob is not None and pred.home_model_prob is not None:
        heuristic_sum = pred.away_model_prob + pred.home_model_prob
        if not (0.99 <= heuristic_sum <= 1.01):
            errors.append(f"away_model_prob+home_model_prob suma {heuristic_sum}, no ~1.0")

    if errors:
        logger.error(f"Validación de probabilidades fallida para game_pk={pred.game_pk}: {errors}")
        return False
    return True


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
            .order_by(GameAnalysis.id.desc())
            .all()
        )
    finally:
        session.close()

    # Dedup por game_pk: desde que GameAnalysis permite una fila por
    # model_version (UniqueConstraint uq_pred), un juego recalculado con
    # una versión nueva del modelo el mismo día tendría dos filas contra
    # el mismo ActualResult -- contaría doble en Brier Score/accuracy si no
    # se filtra. Nos quedamos con la fila más reciente (mayor id) por juego.
    seen_pks = set()
    deduped_rows = []
    for pred, result in rows:
        if pred.game_pk in seen_pks:
            continue
        seen_pks.add(pred.game_pk)
        deduped_rows.append((pred, result))
    rows = deduped_rows

    validated_rows = [(pred, result) for pred, result in rows if validate_probabilities(pred)]
    n_invalid = len(rows) - len(validated_rows)
    if n_invalid:
        logger.warning(f"{n_invalid} predicción(es) excluida(s) de las métricas por validación de rango fallida")
    rows = validated_rows

    if not rows:
        return {"n_games": 0, "accuracy": None, "brier_score": None}

    correct = 0
    brier_sum = 0.0
    log_loss_sum = 0.0
    market_brier_sum = 0.0
    market_n = 0

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

        # Benchmark: Brier Score del consenso de mercado sin vig, cuando
        # hubo cuotas ese día. Si el modelo no le gana esto, probablemente
        # no tiene edge real — es ruido, no señal.
        if pred.home_market_no_vig_prob is not None:
            market_brier_sum += (pred.home_market_no_vig_prob - actual_home_win) ** 2
            market_n += 1

    n = len(rows)
    return {
        "n_games": n,
        "accuracy": correct / n,
        "brier_score": brier_sum / n,
        "log_loss": log_loss_sum / n,
        "market_brier_score": (market_brier_sum / market_n) if market_n else None,
        "market_n_games": market_n,
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


def compute_pick_performance(days: int = 30) -> dict:
    """
    Desempeño de los Picks generados por el sistema — en unidades
    NOCIONALES (1u pareja por pick), NO dinero real (eso vive en
    compute_bet_performance). Separa picks reales (forced=False) de picks
    forzados (forced=True) para no diluir la señal real con el relleno de
    la regla "siempre al menos 1 pick por partido" — mezclarlos haría que
    un mal pick forzado (sin edge) se vea como una falla del modelo real.
    """
    from datetime import date, timedelta

    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

    session = SessionLocal()
    try:
        picks = (
            session.query(Pick)
            .filter(Pick.game_date >= cutoff, Pick.result.in_(["win", "loss", "push"]))
            .all()
        )
    finally:
        session.close()

    def _summarize(subset: list) -> dict:
        if not subset:
            return {"n_picks": 0, "win_rate": None, "roi": None}
        decided = [p for p in subset if p.result != "push"]
        wins = sum(1 for p in decided if p.result == "win")
        total_profit = sum(p.profit_unit for p in subset if p.profit_unit is not None)
        return {
            "n_picks": len(subset),
            "win_rate": (wins / len(decided)) if decided else None,
            "roi": (total_profit / len(subset)) if subset else None,
        }

    by_market = {}
    for market in ("moneyline", "run_line", "totals"):
        market_picks = [p for p in picks if p.market == market]
        by_market[market] = {
            "real": _summarize([p for p in market_picks if not p.forced]),
            "forced": _summarize([p for p in market_picks if p.forced]),
        }

    return {
        "overall_real": _summarize([p for p in picks if not p.forced]),
        "overall_forced": _summarize([p for p in picks if p.forced]),
        "by_market": by_market,
    }


def compute_clv_performance(days: int = 30) -> dict:
    """
    Closing Line Value promedio de las apuestas que ya tienen cuota de
    cierre registrada (ver db.database.record_closing_odds). CLV positivo
    sostenido en el tiempo es la señal más confiable de que el modelo tiene
    skill real, no solo suerte de muestra chica — a diferencia de accuracy
    o incluso ROI, que una racha corta puede inflar o hundir sin decir nada
    sobre si el modelo es bueno.
    """
    from datetime import date, timedelta

    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

    session = SessionLocal()
    try:
        bets_with_clv = (
            session.query(Bet)
            .filter(Bet.game_date >= cutoff, Bet.clv.isnot(None))
            .all()
        )
    finally:
        session.close()

    if not bets_with_clv:
        return {"n_bets": 0, "avg_clv": None}

    avg_clv = sum(b.clv for b in bets_with_clv) / len(bets_with_clv)
    positive_clv = sum(1 for b in bets_with_clv if b.clv > 0)

    return {
        "n_bets": len(bets_with_clv),
        "avg_clv": avg_clv,
        "positive_clv_rate": positive_clv / len(bets_with_clv),
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

    if metrics.get("market_brier_score") is not None:
        diff = metrics["market_brier_score"] - metrics["brier_score"]
        veredicto = "tu modelo le gana al mercado" if diff > 0 else "el mercado le gana a tu modelo"
        print(f"Brier del mercado: {metrics['market_brier_score']:.4f}  "
              f"(consenso sin vig, {metrics['market_n_games']} juegos con cuota) — {veredicto}")

    pick_perf = compute_pick_performance(days=days)
    print()
    if pick_perf["overall_real"]["n_picks"] == 0 and pick_perf["overall_forced"]["n_picks"] == 0:
        print("Todavía no hay picks liquidados.")
    else:
        print("--- Picks del sistema (unidades nocionales, no dinero real) ---")
        real, forced = pick_perf["overall_real"], pick_perf["overall_forced"]
        if real["n_picks"]:
            print(f"Reales (con edge):   {real['n_picks']} picks, "
                  f"{real['win_rate']:.1%} win rate, ROI {real['roi']:+.1%}")
        if forced["n_picks"]:
            print(f"Forzados (sin edge): {forced['n_picks']} picks, "
                  f"{forced['win_rate']:.1%} win rate, ROI {forced['roi']:+.1%}  "
                  f"(relleno, no mezclar con el desempeño real)")
        for market, perf in pick_perf["by_market"].items():
            n_real, n_forced = perf["real"]["n_picks"], perf["forced"]["n_picks"]
            if not n_real and not n_forced:
                continue
            parts = []
            if n_real:
                parts.append(f"reales={n_real} (ROI {perf['real']['roi']:+.1%})")
            if n_forced:
                parts.append(f"forzados={n_forced} (ROI {perf['forced']['roi']:+.1%})")
            print(f"  {market}: " + "  |  ".join(parts))

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

    clv_perf = compute_clv_performance(days=days)
    if clv_perf["n_bets"] > 0:
        print()
        print(f"--- Closing Line Value (CLV) ---")
        print(f"Apuestas con cuota de cierre registrada: {clv_perf['n_bets']}")
        print(f"CLV promedio:        {clv_perf['avg_clv']:+.1%}  "
              f"(positivo = bateaste la línea de cierre)")
        print(f"% con CLV positivo:  {clv_perf['positive_clv_rate']:.1%}")
    print()


def audit_totals() -> dict:
    """
    MAE (error absoluto medio) de carreras totales proyectadas vs. reales,
    para los juegos que ya tienen resultado. Reemplaza al antiguo
    audit_live.py: mismo reporte, pero vía SessionLocal (respeta
    DATABASE_URL) en vez de sqlite3.connect('mlb_edge.db') hardcodeado —
    ese hardcodeo hacía que el reporte auditara una base de datos SQLite
    fija incluso si el proyecto corría contra PostgreSQL.
    """
    session = SessionLocal()
    try:
        rows = (
            session.query(GameAnalysis, ActualResult)
            .outerjoin(ActualResult, GameAnalysis.game_pk == ActualResult.game_pk)
            .all()
        )
    finally:
        session.close()

    print(f"\n{'=' * 95}")
    print(f"{'AUDITORÍA DE PRECISIÓN (TOTALES)':^95}")
    print(f"{'=' * 95}")
    print(f"{'Juego':<30} | {'Pred':<6} | {'Real':<6} | {'Dif (Error)'}")
    print("-" * 95)

    total_mae = 0.0
    count = 0

    for pred, result in rows:
        matchup = f"{pred.away_team} @ {pred.home_team}"
        if result is not None and result.total_runs is not None and pred.away_proj_runs is not None and pred.home_proj_runs is not None:
            proj_total = pred.away_proj_runs + pred.home_proj_runs
            diff = abs(proj_total - result.total_runs)
            total_mae += diff
            count += 1
            print(f"{matchup:<30} | {proj_total:<6.2f} | {result.total_runs:<6} | {diff:<10.2f}")
        else:
            print(f"{matchup:<30} | {'PENDIENTE':<6} | {'-':<6} | {'-'}")

    mae = (total_mae / count) if count else None
    if count:
        print("-" * 95)
        print(f"ERROR PROMEDIO (MAE): {mae:.2f} carreras")
    print()

    return {"n_games": count, "mae": mae}
