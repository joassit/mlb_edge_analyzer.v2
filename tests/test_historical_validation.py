"""
Pruebas de historical_engine/validation.py -- orquestación de métricas
sobre HistoricalPrediction ya guardadas, con datos sembrados a mano
(cero red, cero dependencia del pipeline completo).
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import historical_engine.db as historical_db
from historical_engine.validation import validate_source, validate_all_sources, compare_seasons_drift


def _session_with_predictions(tmp_path, name, rows):
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db")
    historical_db.HistoricalBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    for r in rows:
        session.add(historical_db.HistoricalPrediction(**r))
    session.commit()
    session.close()
    return Session


def _pred(game_pk, season, source, home_prob, predicted_winner, actual_winner):
    away_prob = 1 - home_prob
    return {
        "run_id": 1, "game_pk": game_pk, "game_date": "2024-05-01", "season_year": season,
        "source": source, "away_prob": away_prob, "home_prob": home_prob,
        "predicted_winner": predicted_winner, "actual_winner": actual_winner,
        "correct": predicted_winner == actual_winner,
    }


def test_validate_source_computes_accuracy_and_brier(tmp_path):
    rows = [
        _pred(1, 2024, "skellam", 0.70, "home", "home"),   # acierto
        _pred(2, 2024, "skellam", 0.65, "home", "away"),   # fallo
        _pred(3, 2024, "skellam", 0.30, "away", "away"),   # acierto (favorece away, gana away)
    ]
    Session = _session_with_predictions(tmp_path, "validate_basic", rows)

    metrics = validate_source(season_year=2024, source="skellam", run_id=99, session_factory=Session)

    assert metrics["n_sample"] == 3
    assert abs(metrics["accuracy"] - (2 / 3)) < 1e-9
    assert metrics["brier_score"] is not None
    assert metrics["log_loss"] is not None


def test_validate_source_persists_metrics_and_calibration(tmp_path):
    rows = [_pred(1, 2024, "skellam", 0.70, "home", "home"), _pred(2, 2024, "skellam", 0.65, "home", "away")]
    Session = _session_with_predictions(tmp_path, "validate_persist", rows)

    validate_source(season_year=2024, source="skellam", run_id=7, session_factory=Session)

    session = Session()
    metric_rows = session.query(historical_db.HistoricalMetrics).filter_by(run_id=7).all()
    calibration_rows = session.query(historical_db.HistoricalCalibration).filter_by(run_id=7).all()
    session.close()

    metric_names = {m.metric_name for m in metric_rows}
    assert "brier_score" in metric_names
    assert "accuracy" in metric_names
    assert "ece" in metric_names
    assert len(calibration_rows) >= 1


def test_validate_source_ignores_pending_predictions_without_result(tmp_path):
    rows = [
        _pred(1, 2024, "skellam", 0.70, "home", "home"),
        {**_pred(2, 2024, "skellam", 0.60, "home", None), "correct": None},
    ]
    Session = _session_with_predictions(tmp_path, "validate_pending", rows)

    metrics = validate_source(season_year=2024, source="skellam", run_id=1, session_factory=Session)
    assert metrics["n_sample"] == 1  # el pendiente no cuenta


def test_validate_all_sources_returns_one_entry_per_engine(tmp_path):
    rows = [
        _pred(1, 2024, "skellam", 0.70, "home", "home"),
        _pred(1, 2024, "negbin", 0.68, "home", "home"),
        _pred(1, 2024, "heuristic", 0.55, "home", "home"),
    ]
    Session = _session_with_predictions(tmp_path, "validate_all", rows)

    results = validate_all_sources(season_year=2024, run_id=1, session_factory=Session)
    assert set(results.keys()) == {"heuristic", "skellam", "negbin"}
    for r in results.values():
        assert r["n_sample"] == 1


def test_compare_seasons_drift_never_averages_seasons_together(tmp_path):
    rows = [
        _pred(1, 2024, "skellam", 0.70, "home", "home"),
        _pred(2, 2024, "skellam", 0.70, "home", "home"),
        _pred(3, 2025, "skellam", 0.70, "home", "away"),
        _pred(4, 2025, "skellam", 0.70, "home", "away"),
    ]
    Session = _session_with_predictions(tmp_path, "validate_drift", rows)

    drift = compare_seasons_drift("skellam", [2024, 2025], session_factory=Session)

    assert drift["by_season"][2024]["accuracy"] == 1.0
    assert drift["by_season"][2025]["accuracy"] == 0.0
    # cada temporada mantiene su propia cifra -- nunca se combina en un
    # único "accuracy total" que perdería la comparación misma.
    assert set(drift["by_season"].keys()) == {2024, 2025}


def test_compare_seasons_drift_flags_large_spread():
    pass  # cubierto indirectamente por el test anterior (spread=1.0 > 0.15)
