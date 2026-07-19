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

from data.mlb_api import get_game_result, find_makeup_game_result
from db.database import (
    SessionLocal, GameAnalysis, ActualResult, Bet, Pick,
    save_result, get_predictions_without_result, settle_bets_for_game, settle_picks_for_game,
    game_pk_has_prediction,
)
# ECE/MCE ya viven en historical_engine/stats_utils.py (funciones puras, sin
# DB ni estado) -- se reutilizan acá en vez de duplicar la fórmula. Esto NO
# viola el aislamiento de historical_engine/ (ver tests/test_historical_isolation.py):
# esa suite prohíbe que historical_engine importe DE producción, no que
# producción importe una función matemática pura de historical_engine.
from historical_engine.stats_utils import expected_calibration_error, maximum_calibration_error
from db.enums import BetResult, PickResult

logger = logging.getLogger("mlb_edge_analyzer")


def update_results(days_back: int = 21) -> int:
    """
    Busca resultados reales para predicciones pasadas que aún no los
    tienen. Devuelve cuántos juegos se actualizaron.

    days_back en 21 (antes 5) -- ver get_predictions_without_result() en
    db/database.py: una ventana corta deja huérfanos para siempre a los
    juegos pospuestos que tardan más de esos días en reanudarse.
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
            # Puede ser que el juego todavía no termine, o que se haya
            # pospuesto y MLB Stats API nunca complete un marcador bajo
            # este mismo game_pk (ver comentario en get_game_result()) --
            # en ese segundo caso, el juego real se reprogramó bajo un
            # game_pk NUEVO, así que se busca ahí antes de rendirse.
            try:
                makeup = find_makeup_game_result(
                    pred["game_pk"], pred["away_team"], pred["home_team"], pred["game_date"]
                )
            except Exception as e:
                logger.warning(f"No se pudo buscar juego de reposición de game_pk={pred['game_pk']}: {e}")
                continue

            if makeup is None:
                continue  # sigue genuinamente pendiente, o no se encontró la reposición

            resolved_pk = makeup.pop("resolved_via_game_pk")
            if game_pk_has_prediction(resolved_pk):
                # El juego de reposición YA fue predicho de forma
                # independiente bajo su propio game_pk (mismo partido
                # real, dos IDs distintos en MLB Stats API) -- copiarle el
                # marcador también a este game_pk original duplicaría el
                # conteo de un mismo juego real en compute_metrics()/ROI.
                # Se deja sin resolver a propósito (huérfano permanente,
                # documentado, no un bug silencioso).
                logger.warning(
                    f"game_pk={pred['game_pk']} ({pred['away_team']} @ {pred['home_team']}, "
                    f"{pred['game_date']}) se pospuso y reprogramó, pero el juego real "
                    f"(game_pk={resolved_pk}) ya fue predicho por separado -- NO se reconcilia "
                    f"para no contar el mismo partido dos veces."
                )
                continue

            result = makeup
            logger.info(
                f"game_pk={pred['game_pk']} ({pred['away_team']} @ {pred['home_team']}): resultado "
                f"recuperado vía juego de reposición game_pk={resolved_pk} (pospuesto originalmente)."
            )

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


# Un modelo por entrada: (campo de prob. del visitante, campo de prob. del
# local). compute_metrics() recorre esto para reportar accuracy/Brier/Log
# Loss de heurístico, Skellam y Binomial Negativo por separado, en vez de
# mezclarlos -- cada modelo puede tener una fila con ese campo en None
# (predicciones guardadas antes de que existiera ese modelo), y eso NO debe
# contarse como una falla del modelo, solo como "sin dato para este juego".
_MODEL_FIELDS = {
    "heuristic": ("away_model_prob", "home_model_prob"),
    "skellam": ("away_skellam_prob", "home_skellam_prob"),
    "negbin": ("away_negbin_prob", "home_negbin_prob"),
}


def validate_probabilities(pred: GameAnalysis) -> bool:
    """
    Sanity check de rango [0,1] y de que las probabilidades del mismo
    modelo sumen ~1 -- protege compute_metrics() de filas corruptas (bug de
    persistencia, migración incompleta, edición manual de la DB) que de
    otro modo inflarían o hundirían el Brier Score/accuracy en silencio en
    vez de saltar a la vista como un dato malo.
    """
    errors = []

    for _, (away_field, home_field) in _MODEL_FIELDS.items():
        for field in (away_field, home_field):
            # getattr con default None -- no todo caller pasa un GameAnalysis
            # real (los tests unitarios usan objetos simples que imitan solo
            # los campos que necesitan), y una fila sin este atributo debe
            # tratarse igual que un valor ausente, no como un error.
            p = getattr(pred, field, None)
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


def _compute_model_metrics(rows: list, home_field: str) -> dict:
    """
    Accuracy/Brier/Log Loss de UN modelo (identificado por su campo de
    probabilidad del local en GameAnalysis), sobre las filas de `rows` que
    sí tienen ese campo -- una fila con home_field=None (modelo agregado
    después de que esa predicción se guardó) se excluye de ESE modelo sin
    afectar a los demás ni contarse como fallida.
    """
    correct = 0
    brier_sum = 0.0
    log_loss_sum = 0.0
    n = 0

    for pred, result in rows:
        home_prob = getattr(pred, home_field, None)
        if home_prob is None:
            continue
        n += 1

        actual_home_win = 1 if result.winner == "home" else 0
        predicted_winner_is_home = home_prob > 0.5
        actual_winner_is_home = actual_home_win == 1

        if predicted_winner_is_home == actual_winner_is_home:
            correct += 1

        brier_sum += (home_prob - actual_home_win) ** 2

        p_clipped = min(max(home_prob, 1e-15), 1 - 1e-15)
        log_loss_sum += -(
            actual_home_win * math.log(p_clipped) +
            (1 - actual_home_win) * math.log(1 - p_clipped)
        )

    if n == 0:
        return {"n_games": 0, "accuracy": None, "brier_score": None, "log_loss": None}

    return {
        "n_games": n,
        "accuracy": correct / n,
        "brier_score": brier_sum / n,
        "log_loss": log_loss_sum / n,
    }


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
        return {"n_games": 0, "accuracy": None, "brier_score": None,
                "by_model": {name: {"n_games": 0, "accuracy": None, "brier_score": None, "log_loss": None}
                             for name in _MODEL_FIELDS}}

    # Defensa contra predicciones duplicadas históricas (versiones previas
    # de save_analysis no eran idempotentes): solo la más reciente por juego.
    latest_by_pk = {}
    for pred, result in rows:
        prev = latest_by_pk.get(pred.game_pk)
        if prev is None or pred.created_at > prev[0].created_at:
            latest_by_pk[pred.game_pk] = (pred, result)
    rows = list(latest_by_pk.values())

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
    # by_model reporta heurístico/Skellam/Binomial Negativo por separado,
    # cada uno solo sobre las filas que sí tienen ese campo -- "heuristic"
    # aquí es un recálculo redundante de lo mismo que ya está arriba (n,
    # accuracy, brier_score, log_loss), a propósito: los campos de nivel
    # superior existen desde antes de que hubiera más de un modelo y no se
    # tocan (otros callers/tests dependen de esas claves), by_model es la
    # extensión aditiva para comparar los tres.
    by_model = {name: _compute_model_metrics(rows, home_field)
                for name, (_, home_field) in _MODEL_FIELDS.items()}

    return {
        "n_games": n,
        "accuracy": correct / n,
        "brier_score": brier_sum / n,
        "log_loss": log_loss_sum / n,
        "market_brier_score": (market_brier_sum / market_n) if market_n else None,
        "market_n_games": market_n,
        "by_model": by_model,
    }


def count_liquidated_picks_with_market_odds() -> int:
    """
    Cuenta cuántos PICKS están liquidados (result != "pending") Y tuvieron
    una cuota de mercado real (odds_used is not None) -- para decidir si
    el histórico de EDGES REALMENTE PROBADOS ya alcanza
    config.MIN_LIQUIDATED_PICKS_FOR_CALIBRATION o el heurístico todavía
    está en fase de calibración (ver Pick.calibration_phase en
    db/database.py).

    Antes (count_evaluated_games_all_time()) esto contaba cualquier juego
    con predicción + resultado final, sin importar si hubo cuota de
    mercado -- eso mide si la PROBABILIDAD cruda del modelo está bien
    calibrada (pregunta que ya responde print_calibration_report()/
    compute_calibration(), sin tocar). Esta función mide algo distinto:
    si el EDGE (model_prob vs. cuota de mercado real) que decide un pick
    es señal o ruido -- un juego sin cuota de mercado nunca puso a prueba
    ningún edge, así que no cuenta aquí aunque sí tenga un resultado real
    y una probabilidad válida.

    Dedup por (game_pk, market), quedándose con la fila de mayor
    created_at: uq_pick_game_market_selection NO evita que el mismo
    game_pk+market quede duplicado si el pick cambió de `selection` entre
    dos corridas del mismo día (el recálculo es una fila nueva, no un
    upsert, porque selection forma parte de la unique key) — mismo
    criterio ya usado en compute_pick_performance()/compute_daily_review().
    """
    session = SessionLocal()
    try:
        picks = (
            session.query(Pick)
            .filter(Pick.odds_used.isnot(None), Pick.result != PickResult.PENDING)
            .all()
        )
    finally:
        session.close()

    latest_by_game_market = {}
    for p in picks:
        key = (p.game_pk, p.market)
        prev = latest_by_game_market.get(key)
        if prev is None or p.created_at > prev.created_at:
            latest_by_game_market[key] = p

    return len(latest_by_game_market)


# Rango [low, high) de confianza en el favorito declarado -- confidence =
# max(prob, 1-prob) siempre cae en [0.5, 1.0], así que el último bucket usa
# 1.01 como tope para incluir el caso límite confidence == 1.0 sin un
# operador distinto solo para ese bucket.
_CALIBRATION_BUCKETS = [
    (0.50, 0.55, "50-55%"),
    (0.55, 0.60, "55-60%"),
    (0.60, 0.65, "60-65%"),
    (0.65, 0.70, "65-70%"),
    (0.70, 0.75, "70-75%"),
    (0.75, 1.01, "75%+"),
]


def _bucket_index_for_confidence(confidence: float) -> int | None:
    for i, (low, high, _label) in enumerate(_CALIBRATION_BUCKETS):
        if low <= confidence < high:
            return i
    return None  # confidence fuera de [0.5, 1.0] -- no debería pasar nunca


def _compute_model_calibration(rows: list, home_field: str) -> dict:
    """
    Calibración de UN modelo (su campo de probabilidad del local) sobre
    `rows` ya deduplicadas y validadas -- agrupa por bucket de confianza en
    el favorito declarado (max(prob, 1-prob)) y compara hit_rate real
    contra la confianza promedio que el modelo dijo tener en ese bucket.

    gap = hit_rate - avg_confidence:
      gap > 0 -> el modelo está SUBconfiado ahí (acierta más de lo que dice
                 creer -- hay value sin explotar).
      gap < 0 -> el modelo está SOBREconfiado ahí (acierta menos de lo que
                 dice creer -- ahí es donde se pierde dinero apostando).
    """
    bucket_totals = [{"n": 0, "hits": 0, "confidence_sum": 0.0} for _ in _CALIBRATION_BUCKETS]
    n_games = 0
    n_skipped = 0

    for pred, result in rows:
        home_prob = getattr(pred, home_field, None)
        if home_prob is None:
            n_skipped += 1
            continue
        n_games += 1

        confidence = max(home_prob, 1.0 - home_prob)
        favorite_is_home = home_prob >= 0.5
        actual_home_win = (result.winner == "home")
        hit = favorite_is_home == actual_home_win

        idx = _bucket_index_for_confidence(confidence)
        if idx is None:
            continue
        bucket_totals[idx]["n"] += 1
        bucket_totals[idx]["hits"] += 1 if hit else 0
        bucket_totals[idx]["confidence_sum"] += confidence

    buckets = []
    for (_low, _high, label), totals in zip(_CALIBRATION_BUCKETS, bucket_totals):
        n = totals["n"]
        if n == 0:
            buckets.append({"label": label, "n": 0, "hits": 0,
                             "hit_rate": None, "avg_confidence": None, "gap": None})
            continue
        hit_rate = totals["hits"] / n
        avg_confidence = totals["confidence_sum"] / n
        buckets.append({
            "label": label, "n": n, "hits": totals["hits"],
            "hit_rate": hit_rate, "avg_confidence": avg_confidence,
            "gap": hit_rate - avg_confidence,
        })

    return {
        "n_games": n_games, "n_skipped": n_skipped, "buckets": buckets,
        "ece": expected_calibration_error(buckets, n_games),
        "mce": maximum_calibration_error(buckets),
    }


def compute_calibration(days: int = 90) -> dict:
    """
    Calibración por bucket de confianza (ver _compute_model_calibration)
    para los 3 modelos de _MODEL_FIELDS, sobre la misma ventana de días,
    mismo dedup por game_pk y misma validate_probabilities() que
    compute_metrics() -- deliberadamente NO comparte código con
    compute_metrics() más allá de eso (no se toca esa función), para que
    un cambio futuro en una no arrastre a la otra por accidente.
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

    seen_pks = set()
    deduped_rows = []
    for pred, result in rows:
        if pred.game_pk in seen_pks:
            continue
        seen_pks.add(pred.game_pk)
        deduped_rows.append((pred, result))

    validated_rows = [(pred, result) for pred, result in deduped_rows if validate_probabilities(pred)]

    return {name: _compute_model_calibration(validated_rows, home_field)
            for name, (_, home_field) in _MODEL_FIELDS.items()}


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
            .filter(Bet.game_date >= cutoff, Bet.result.in_([BetResult.WIN, BetResult.LOSS]))
            .all()
        )
    finally:
        session.close()

    if not settled:
        return {"n_bets": 0, "total_staked": None, "total_profit": None, "roi": None}

    total_staked = sum(b.stake for b in settled)
    total_profit = sum(b.profit for b in settled)
    wins = sum(1 for b in settled if b.result == BetResult.WIN)

    return {
        "n_bets": len(settled),
        "wins": wins,
        "win_rate": wins / len(settled),
        "total_staked": total_staked,
        "total_profit": total_profit,
        "roi": (total_profit / total_staked) if total_staked > 0 else None,
    }


def _summarize_picks(subset: list) -> dict:
    """
    Hit rate y ROI nocional (1u pareja por pick) de una lista de Pick ya
    liquidados (result en win/loss/push). Módulo-level a propósito: la
    usan tanto compute_pick_performance() (ventana rodante de N días) como
    compute_daily_review() (un solo día) -- misma definición de hit
    rate/ROI en ambos, nunca dos fórmulas que diverjan con el tiempo.
    """
    if not subset:
        return {"n_picks": 0, "win_rate": None, "roi": None}
    decided = [p for p in subset if p.result != PickResult.PUSH]
    wins = sum(1 for p in decided if p.result == PickResult.WIN)
    total_profit = sum(p.profit_unit for p in subset if p.profit_unit is not None)
    return {
        "n_picks": len(subset),
        "win_rate": (wins / len(decided)) if decided else None,
        "roi": (total_profit / len(subset)) if subset else None,
    }


def compute_pick_performance(days: int = 30) -> dict:
    """
    Desempeño de los Picks generados por el sistema — en unidades
    NOCIONALES (1u pareja por pick), NO dinero real (eso vive en
    compute_bet_performance). Separa picks reales (forced=False) de picks
    forzados (forced=True) para no diluir la señal real con el relleno de
    la regla "siempre al menos 1 pick por partido" — mezclarlos haría que
    un mal pick forzado (sin edge) se vea como una falla del modelo real.

    Dedup por (game_pk, market), quedándose con la fila de mayor
    created_at: uq_pick_game_market_selection NO evita que el mismo
    game_pk+market quede duplicado si el pick cambió de `selection` entre
    dos corridas del mismo día (el recálculo es una fila nueva, no un
    upsert, porque selection forma parte de la unique key) — sin este
    dedup, ese único juego se cuenta dos veces. Por market, no por
    game_pk solo: un mismo partido puede tener hasta 3 picks legítimos y
    distintos (moneyline/run_line/totals) que nunca deben perderse.
    """
    from datetime import date, timedelta

    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

    session = SessionLocal()
    try:
        picks = (
            session.query(Pick)
            .filter(Pick.game_date >= cutoff, Pick.result.in_([PickResult.WIN, PickResult.LOSS, PickResult.PUSH]))
            .all()
        )
    finally:
        session.close()

    latest_by_game_market = {}
    for p in picks:
        key = (p.game_pk, p.market)
        prev = latest_by_game_market.get(key)
        if prev is None or p.created_at > prev.created_at:
            latest_by_game_market[key] = p
    picks = list(latest_by_game_market.values())

    by_market = {}
    for market in ("moneyline", "run_line", "totals"):
        market_picks = [p for p in picks if p.market == market]
        by_market[market] = {
            "real": _summarize_picks([p for p in market_picks if not p.forced]),
            "forced": _summarize_picks([p for p in market_picks if p.forced]),
        }

    return {
        "overall_real": _summarize_picks([p for p in picks if not p.forced]),
        "overall_forced": _summarize_picks([p for p in picks if p.forced]),
        "by_market": by_market,
    }


def _pick_to_dict(pick: "Pick | None") -> dict | None:
    """Representación plana de un Pick liquidado, para el reporte diario
    -- reports/generate_report.py no debe importar el modelo SQLAlchemy
    solo para leer estos campos."""
    if pick is None:
        return None
    return {
        "market": pick.market,
        "selection": pick.selection,
        "line": pick.line,
        "model_prob": pick.model_prob,
        "forced": pick.forced,
        "result": pick.result,
        "profit_unit": pick.profit_unit,
        "odds_used": pick.odds_used,
    }


def compute_daily_review(review_date: str) -> dict:
    """
    Revisión detallada de UN día específico (normalmente ayer): por
    partido, con los 3 mercados (moneyline/run_line/totals) uno al lado
    del otro -- a diferencia de compute_metrics()/compute_pick_performance()
    (ventana rodante de N días), esto es exactamente un día, pensado para
    la Sección 1 del reporte diario.

    Reutiliza el mismo criterio de dedup por game_pk (se queda con la fila
    de mayor id si el juego se recalculó con una model_version nueva ese
    día) y la misma validate_probabilities() que compute_metrics(), para
    que el Brier Score de este reporte no se calcule sobre una fila
    corrupta ni cuente un juego recalculado dos veces.

    Los Pick también se dedupean, por (game_pk, market) quedándose con el
    de mayor created_at -- mismo motivo que en compute_pick_performance():
    un recálculo intradía que cambia `selection` no choca con
    uq_pick_game_market_selection, así que puede haber más de una fila
    para el mismo game_pk+market. Sin esto, tanto la ficha del partido
    (picks_by_game) como by_market podían mostrar/contar una fila
    arbitraria (la última en el orden de iteración, no necesariamente la
    más reciente) o contar el mismo juego dos veces.
    """
    session = SessionLocal()
    try:
        analysis_rows = (
            session.query(GameAnalysis)
            .filter(GameAnalysis.game_date == review_date)
            .order_by(GameAnalysis.id.desc())
            .all()
        )
        results_by_pk = {
            r.game_pk: r
            for r in session.query(ActualResult).filter(ActualResult.game_date == review_date).all()
        }
        picks = (
            session.query(Pick)
            .filter(Pick.game_date == review_date, Pick.result != PickResult.PENDING)
            .all()
        )
    finally:
        session.close()

    seen_pks = set()
    deduped_analysis = []
    for pred in analysis_rows:
        if pred.game_pk in seen_pks:
            continue
        seen_pks.add(pred.game_pk)
        deduped_analysis.append(pred)

    latest_by_game_market = {}
    for p in picks:
        key = (p.game_pk, p.market)
        prev = latest_by_game_market.get(key)
        if prev is None or p.created_at > prev.created_at:
            latest_by_game_market[key] = p
    picks = list(latest_by_game_market.values())

    picks_by_game = {}
    for p in picks:
        picks_by_game.setdefault(p.game_pk, {})[p.market] = p

    games = []
    brier_sum = 0.0
    brier_n = 0

    for pred in deduped_analysis:
        result = results_by_pk.get(pred.game_pk)
        if result is None:
            continue  # el juego de ayer todavía no tiene marcador real (raro, pero posible)
        if not validate_probabilities(pred):
            continue

        proj_margin = proj_total = None
        if pred.away_proj_runs is not None and pred.home_proj_runs is not None:
            proj_margin = pred.home_proj_runs - pred.away_proj_runs
            proj_total = pred.away_proj_runs + pred.home_proj_runs

        game_picks = picks_by_game.get(pred.game_pk, {})
        games.append({
            "game_pk": pred.game_pk,
            "away_team": pred.away_team, "home_team": pred.home_team,
            "away_score": result.away_score, "home_score": result.home_score,
            "actual_margin": result.home_score - result.away_score,
            "actual_total": result.total_runs,
            "proj_margin": proj_margin, "proj_total": proj_total,
            "picks": {
                "moneyline": _pick_to_dict(game_picks.get("moneyline")),
                "run_line": _pick_to_dict(game_picks.get("run_line")),
                "totals": _pick_to_dict(game_picks.get("totals")),
            },
        })

        if pred.home_model_prob is not None:
            actual_home_win = 1 if result.winner == "home" else 0
            brier_sum += (pred.home_model_prob - actual_home_win) ** 2
            brier_n += 1

    by_market = {}
    for market in ("moneyline", "run_line", "totals"):
        market_picks = [p for p in picks if p.market == market]
        by_market[market] = {
            "real": _summarize_picks([p for p in market_picks if not p.forced]),
            "forced": _summarize_picks([p for p in market_picks if p.forced]),
        }

    return {
        "review_date": review_date,
        "n_games": len(games),
        "games": games,
        "by_market": by_market,
        "brier_score": (brier_sum / brier_n) if brier_n else None,
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

    by_model = metrics.get("by_model") or {}
    _MODEL_LABELS = {"heuristic": "Heurístico", "skellam": "Skellam", "negbin": "Binomial Negativo"}
    if any(by_model.get(name, {}).get("n_games") for name in _MODEL_LABELS):
        print("\n--- Comparación de modelos (mismos juegos, cuando el dato existe) ---")
        for name, label in _MODEL_LABELS.items():
            m = by_model.get(name, {})
            if not m.get("n_games"):
                print(f"{label:<18} sin datos todavía (el modelo se agregó después de estas predicciones)")
                continue
            print(f"{label:<18} n={m['n_games']:<4} accuracy={m['accuracy']:.1%}  "
                  f"brier={m['brier_score']:.4f}  log_loss={m['log_loss']:.4f}")

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


_CALIBRATION_MODEL_LABELS = {"heuristic": "Heurístico", "skellam": "Skellam", "negbin": "Binomial Negativo"}
_SMALL_SAMPLE_THRESHOLD = 20


def print_calibration_report(days: int = 90) -> None:
    """
    Calibración por bucket de confianza (ver compute_calibration) para los
    3 modelos -- ¿cuándo el modelo dice "60% de confianza", de verdad
    acierta 60% de las veces? Un gap negativo sostenido en un bucket es
    donde el modelo está sobreconfiado y perdería dinero apostando ahí
    aunque su accuracy general se vea bien.
    """
    calibration = compute_calibration(days=days)

    print(f"\n{'=' * 78}")
    print(f"{'CALIBRACIÓN POR BUCKET DE CONFIANZA — últimos ' + str(days) + ' días':^78}")
    print(f"{'=' * 78}")

    for name, label in _CALIBRATION_MODEL_LABELS.items():
        model_cal = calibration.get(name, {"n_games": 0, "n_skipped": 0, "buckets": []})
        print(f"\n--- {label} ---")

        if model_cal["n_games"] == 0:
            skip_note = f" ({model_cal['n_skipped']} predicción(es) sin este dato, omitidas)" if model_cal["n_skipped"] else ""
            print(f"Sin datos suficientes todavía.{skip_note}")
            continue

        print(f"{'Rango':<8} | {'N':>4} | {'Aciertos':>8} | {'Efectividad':>11} | {'Confianza decl.':>15} | {'Gap':>7}")
        print("-" * 78)
        for b in model_cal["buckets"]:
            if b["n"] == 0:
                print(f"{b['label']:<8} | {0:>4} | {'-':>8} | {'-':>11} | {'-':>15} | {'-':>7}")
                continue
            nota = "  (muestra chica)" if b["n"] < _SMALL_SAMPLE_THRESHOLD else ""
            print(f"{b['label']:<8} | {b['n']:>4} | {b['hits']:>8} | {b['hit_rate']:>10.1%} | "
                  f"{b['avg_confidence']:>14.1%} | {b['gap']:>+6.1%}{nota}")

        if model_cal["n_skipped"]:
            print(f"({model_cal['n_skipped']} predicción(es) sin probabilidad de este modelo, omitidas de arriba)")

        if model_cal.get("ece") is not None:
            print(f"ECE (error de calibración esperado): {model_cal['ece']:.1%}   "
                  f"MCE (peor bucket): {model_cal['mce']:.1%}")
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
