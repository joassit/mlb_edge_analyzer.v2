"""
Pruebas de historical_engine/model_comparison.py -- la garantía central es
que nunca elige un "ganador" automático (no existe ningún campo tipo
"winner"/"best_model" en la salida), solo tabula y observa.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import historical_engine.db as historical_db
from historical_engine.model_comparison import compare_models


def _seeded(tmp_path, name):
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db")
    historical_db.HistoricalBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    rows = [
        ("skellam", 0.70, "home", "home"), ("skellam", 0.65, "home", "home"), ("skellam", 0.60, "home", "away"),
        ("negbin", 0.68, "home", "home"), ("negbin", 0.62, "home", "away"),
        ("heuristic", 0.55, "home", "away"), ("heuristic", 0.52, "home", "home"),
    ]
    for i, (source, home_prob, predicted, actual) in enumerate(rows):
        session.add(historical_db.HistoricalPrediction(
            run_id=1, game_pk=i, game_date="2024-05-01", season_year=2024, source=source,
            away_prob=1 - home_prob, home_prob=home_prob,
            predicted_winner=predicted, actual_winner=actual, correct=predicted == actual,
        ))
    session.commit()
    session.close()
    return Session


def test_compare_models_includes_all_four_engines(tmp_path):
    Session = _seeded(tmp_path, "compare_basic")
    result = compare_models(season_year=2024, run_id=1, session_factory=Session)

    assert set(result["table"].keys()) == {"heuristic", "skellam", "negbin", "historical_confidence_engine"}


def test_compare_models_never_declares_a_winner_field():
    # Ninguna clave de nivel superior ni de cada entrada de la tabla puede
    # llamarse algo que implique una selección automática.
    forbidden = {"winner", "best_model", "recommended", "selected_model"}
    from historical_engine.model_comparison import compare_models
    import inspect
    source = inspect.getsource(compare_models)
    for word in forbidden:
        assert word not in source


def test_historical_confidence_engine_entry_is_explicitly_not_comparable(tmp_path):
    Session = _seeded(tmp_path, "compare_hce_note")
    result = compare_models(season_year=2024, run_id=1, session_factory=Session)

    hce_entry = result["table"]["historical_confidence_engine"]
    assert hce_entry["accuracy"] is None
    assert "no genera probabilidades" in hce_entry["note"]


def test_observations_never_pick_a_single_winner_string(tmp_path):
    Session = _seeded(tmp_path, "compare_observations")
    result = compare_models(season_year=2024, run_id=1, session_factory=Session)

    assert isinstance(result["observations"], list)
    assert len(result["observations"]) >= 1
    joined = " ".join(result["observations"]).lower()
    assert "el mejor modelo es" not in joined
    assert "ganador" not in joined
