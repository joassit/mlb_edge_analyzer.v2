"""Persiste, por juego, la probabilidad de que gane el LOCAL segun cada
modelo ya benchmarkeado en `validation.py` (jsa_evidence_engine + los 3
modelos legado) -- nunca reimplementa la logica de prediccion, solo
reusa `_legacy_predictions()` y el `raw_probability` ya calculado por el
Evidence Engine, capturando el detalle por-juego que `benchmark_season()`
descarta al agregar directo a `MetricSummary`.

Motivo (2026-07-21): el proyecto hermano `JSA_V2_PROJECT` mantiene un
principio explicito de aislamiento (nunca importar codigo de `jsa/`, solo
lectura de datos vía su rol de Postgres `jsa_v2`) -- para que pueda
comparar por su cuenta contra estos modelos necesita el DATO (la
prediccion por juego), no el codigo que la genera. Ver
`historical_model_prediction` en `db.py` y la nota de GRANT pendiente en
`jsa/docs/ROADMAP.md`."""

from __future__ import annotations

from jsa.domain.models import GameSnapshot
from jsa.historical import db as historical_db
from jsa.historical.validation import _legacy_predictions


def backfill_season(season: int, historical_database_url: str) -> dict:
    """Idempotente (upsert por `(game_pk, model_name)`) -- re-correr tras
    un cambio de logica en un modelo actualiza el valor, nunca duplica ni
    requiere borrar antes. Acumula todas las filas de la temporada en
    memoria y las escribe con UN solo `bulk_upsert_model_predictions()`
    (una transaccion, `executemany` real) -- la version fila-por-fila
    (una transaccion de red por juego por modelo) nunca termino en el
    timeout de 60 min corriendo contra las 5 temporadas reales (medido en
    vivo, run cancelado 2026-07-21)."""
    engine = historical_db.get_engine(historical_database_url)
    historical_db.init_historical_storage(engine)
    games = {g["game_pk"]: g for g in historical_db.games_for_season(engine, season)}
    snapshots = {s["game_pk"]: s for s in historical_db.snapshots_for_season(engine, season)}
    reports = historical_db.reports_for_season(engine, season)

    if not games:
        return {"season": season, "n_games": 0, "n_predictions_written": 0, "error": "sin juegos ingeridos -- correr jsa.historical.pipeline primero"}

    rows: list[dict] = []
    n_games_scored = 0
    for report_row in reports:
        game_pk = report_row["game_pk"]
        game = games.get(game_pk)
        snap_row = snapshots.get(game_pk)
        if game is None or game.get("winner") is None or snap_row is None:
            continue

        report_payload = report_row["payload"]
        raw_prob = report_payload.get("calibration", {}).get("raw_probability")
        if raw_prob is not None:
            rows.append({"game_pk": game_pk, "model_name": "jsa_evidence_engine", "home_win_prob": raw_prob})

        snapshot = GameSnapshot(**snap_row["payload"])
        legacy = _legacy_predictions(snapshot)
        for model_name, prob in legacy.items():
            if prob is not None:
                rows.append({"game_pk": game_pk, "model_name": model_name, "home_win_prob": prob})

        n_games_scored += 1

    n_predictions_written = historical_db.bulk_upsert_model_predictions(engine, season, rows)

    return {"season": season, "n_games": len(games), "n_games_scored": n_games_scored, "n_predictions_written": n_predictions_written}
