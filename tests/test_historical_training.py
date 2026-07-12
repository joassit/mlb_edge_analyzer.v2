"""
Pruebas de historical_engine/training.py -- la garantía central es que
NUNCA aplica un cambio a producción (config.NEGBIN_DISPERSION debe seguir
igual antes y después de correr esto).
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config as production_config
import historical_engine.db as historical_db
from historical_engine.training import propose_dispersion_recalibration


def _seeded(tmp_path, name):
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db")
    historical_db.HistoricalBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    games = [(1, "home"), (2, "away"), (3, "home"), (4, "home"), (5, "away")]
    for game_pk, winner in games:
        session.add(historical_db.HistoricalGame(
            run_id=1, game_pk=game_pk, game_date="2024-05-01", season_year=2024,
            away_team="A", home_team="B", winner=winner,
            home_score=5 if winner == "home" else 2, away_score=2 if winner == "home" else 5,
        ))
        session.add(historical_db.HistoricalAnalysis(
            run_id=1, game_pk=game_pk, game_date="2024-05-01", season_year=2024, as_of_date="2024-04-30",
            home_proj_runs=4.5, away_proj_runs=3.8,
        ))
    session.commit()
    session.close()
    return Session


def test_training_never_modifies_production_config(tmp_path):
    original_dispersion = production_config.NEGBIN_DISPERSION

    Session = _seeded(tmp_path, "training_no_mutation")
    propose_dispersion_recalibration(season_year=2024, run_id=1, session_factory=Session)

    assert production_config.NEGBIN_DISPERSION == original_dispersion


def test_training_always_marks_applied_false(tmp_path):
    Session = _seeded(tmp_path, "training_applied_false")
    result = propose_dispersion_recalibration(season_year=2024, run_id=1, session_factory=Session)

    assert result["applied"] is False

    session = Session()
    sims = session.query(historical_db.HistoricalSimulation).all()
    session.close()
    assert len(sims) > 0
    assert all(sim.applied is False for sim in sims)


def test_training_returns_a_proposal_per_candidate_value(tmp_path):
    Session = _seeded(tmp_path, "training_candidates")
    candidates = [3.0, 7.0, 15.0]
    result = propose_dispersion_recalibration(
        season_year=2024, run_id=1, candidate_values=candidates, session_factory=Session,
    )
    assert len(result["proposals"]) == 3
    assert {p["param_value"] for p in result["proposals"]} == set(candidates)


def test_training_uses_real_negbin_win_prob_function(tmp_path):
    # Verifica indirectamente que se usó model.negbin_model.negbin_win_prob
    # real: con mu fijo (4.5 home, 3.8 away) y k distintos, el Brier score
    # debe variar entre candidatos (si usara un stub que siempre devuelve
    # 0.5, todos los candidatos darían el mismo Brier).
    Session = _seeded(tmp_path, "training_real_function")
    result = propose_dispersion_recalibration(
        season_year=2024, run_id=1, candidate_values=[3.0, 20.0], session_factory=Session,
    )
    briers = [p["brier_score"] for p in result["proposals"]]
    assert briers[0] != briers[1]
