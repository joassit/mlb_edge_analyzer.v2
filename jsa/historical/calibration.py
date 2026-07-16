"""Calibracion isotonica del Evidence Score -- Seccion 8.4.1/9.2. Ajusta
`IsotonicRegression(evidence_score_raw -> P(home wins))` directamente
sobre las temporadas ya ingeridas, validado via leave-one-season-out
(nunca declara "calibrado" sobre la base de un unico fit evaluado contra
sus propios datos de entrenamiento -- Seccion 10.4: walk-forward de >=3
temporadas, n>=50 juegos/temporada).

Por que isotonic regression directo sobre `evidence_score_raw`, sin pasar
por una funcion sigmoide intermedia: isotonic regression no necesita que
su entrada YA sea una probabilidad, solo que este monotonamente
relacionada con el resultado -- exactamente lo que es `evidence_score_raw`
(suma ponderada de advantages discretos por pilar, Seccion 8.1). Evita
inventar una transformacion logistica sin calibrar como paso intermedio
entre el score y la curva real.

Este modulo SOLO ajusta y valida -- nunca decide por si mismo si el
sistema "esta calibrado" para produccion; eso lo hace
`engine/orchestrator.py` leyendo `CalibrationRegistryEntry.status`, la
UNICA fuente de verdad, puesta aqui en base a metricas reales."""

from __future__ import annotations

import logging

from sklearn.isotonic import IsotonicRegression

from jsa.historical import db as historical_db
from jsa.historical.validation import accuracy, brier_score, ece, log_loss, mce

logger = logging.getLogger("jsa.historical")

MIN_GAMES_PER_SEASON = 50  # Seccion 10.4
MIN_SEASONS_FOR_WALK_FORWARD = 3  # Seccion 10.4


def season_evidence_pairs(engine, season: int) -> list[tuple[float, int]]:
    """(evidence_score_raw, actual_home_win) de una temporada -- mismo
    join game_pk<->report<->winner que `validation.py::benchmark_season`,
    pero leyendo `evidence_score_raw` (campo de nivel superior del
    reporte) en vez de `calibration.raw_probability` (que hoy viene del
    modelo Skellam, Seccion 9 -- un modulo separado del Evidence Engine,
    ver ROADMAP)."""
    games = {g["game_pk"]: g for g in historical_db.games_for_season(engine, season)}
    reports = historical_db.reports_for_season(engine, season)

    pairs: list[tuple[float, int]] = []
    for report_row in reports:
        game = games.get(report_row["game_pk"])
        if game is None or game.get("winner") is None:
            continue
        actual_home_win = 1 if game["winner"] == "home" else 0
        evidence_score_raw = report_row["payload"].get("evidence_score_raw")
        if evidence_score_raw is not None:
            pairs.append((float(evidence_score_raw), actual_home_win))
    return pairs


def _fit_isotonic(pairs: list[tuple[float, int]]) -> IsotonicRegression:
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    model = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    model.fit(xs, ys)
    return model


def fit_and_validate(seasons: list[int], historical_database_url: str) -> dict:
    """Punto de entrada principal -- devuelve un dict listo para persistir
    como `CalibrationRegistryEntry`. Incluye tanto la curva de PRODUCCION
    (ajustada sobre TODAS las `seasons` disponibles, para desplegar) como
    las metricas LOSO agregadas (cada temporada evaluada SOLO por un
    modelo que nunca la vio durante su propio ajuste -- nunca la curva de
    produccion evaluada contra sus propios datos de entrenamiento, eso
    seria un numero optimista y no representativo)."""
    engine = historical_db.get_engine(historical_database_url)
    historical_db.init_historical_storage(engine)

    pairs_by_season: dict[int, list[tuple[float, int]]] = {s: season_evidence_pairs(engine, s) for s in seasons}
    games_per_season = {s: len(pairs_by_season[s]) for s in seasons}
    seasons_with_enough_data = [s for s in seasons if games_per_season[s] >= MIN_GAMES_PER_SEASON]

    loso_pairs: list[tuple[float, int]] = []
    loso_seasons_validated: list[int] = []
    for held_out in seasons_with_enough_data:
        training_pairs = [p for s in seasons_with_enough_data if s != held_out for p in pairs_by_season[s]]
        if not training_pairs:
            continue
        model = _fit_isotonic(training_pairs)
        held_out_pairs = pairs_by_season[held_out]
        predictions = model.predict([p[0] for p in held_out_pairs])
        loso_pairs.extend((float(pred), y) for pred, (_, y) in zip(predictions, held_out_pairs))
        loso_seasons_validated.append(held_out)

    # Curva final de PRODUCCION: ajustada sobre TODA la muestra disponible
    # de las temporadas pedidas (no solo las que cuentan para walk-forward
    # -- mas datos ayudan a la curva final igual, aunque una temporada
    # parcial no valide el proceso LOSO por si sola).
    all_pairs = [p for s in seasons for p in pairs_by_season[s]]
    production_model = _fit_isotonic(all_pairs) if all_pairs else None

    if len(loso_seasons_validated) >= MIN_SEASONS_FOR_WALK_FORWARD:
        status = "validated"
    elif not seasons_with_enough_data:
        status = "rejected_insufficient_data"
    else:
        status = "under_validation"

    xs_all = [p[0] for p in all_pairs]
    result = {
        "seasons_used": seasons,
        "games_per_season": games_per_season,
        "n_games_fitted": len(all_pairs),
        "x_min": min(xs_all) if xs_all else 0.0,
        "x_max": max(xs_all) if xs_all else 0.0,
        "x_knots": [float(x) for x in production_model.X_thresholds_] if production_model is not None else [],
        "y_knots": [float(y) for y in production_model.y_thresholds_] if production_model is not None else [],
        "loso_seasons_validated": loso_seasons_validated,
        "loso_n_games": len(loso_pairs),
        "loso_brier": brier_score(loso_pairs),
        "loso_log_loss": log_loss(loso_pairs),
        "loso_accuracy": accuracy(loso_pairs),
        "loso_ece": ece(loso_pairs),
        "loso_mce": mce(loso_pairs),
        "status": status,
    }
    logger.info(
        "fit_and_validate: %d/%d temporadas pasaron walk-forward (%s) -- status=%s, loso_n=%d, loso_brier=%s, loso_ece=%s",
        len(loso_seasons_validated), len(seasons), loso_seasons_validated, status, len(loso_pairs),
        result["loso_brier"], result["loso_ece"],
    )
    return result
