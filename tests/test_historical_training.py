"""
Pruebas de historical_engine/training.py -- la garantía central es que
NUNCA aplica un cambio a producción (config.NEGBIN_DISPERSION debe seguir
igual antes y después de correr esto).
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config as production_config
import historical_engine.db as historical_db
from historical_engine.training import (
    propose_dispersion_recalibration,
    propose_probability_shrinkage,
    propose_runs_projection_recalibration,
    propose_starter_weight_recalibration,
    _recompute_mu_with_candidate,
    _raw_inputs_for_starter_weight_candidate,
)


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


# --- propose_runs_projection_recalibration (PARK_FACTOR_WEIGHT/WEATHER_CORRECTION) ---
#
# Ambos parámetros están NEUTRALIZADOS en producción hoy (PARK_FACTOR_WEIGHT=1.0,
# WEATHER_CORRECTION=0.0, ver config.py) -- la ingesta congeló home_proj_runs/
# away_proj_runs con esos pesos EXACTOS, así que sembrar con park_factor != 1.0
# reproduce fielmente lo que _recompute_mu_with_candidate() tiene que invertir.

def _seeded_with_park_factor(tmp_path, name, park_factor=1.10, temp_f=90.0):
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db")
    historical_db.HistoricalBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    games = [(1, "home"), (2, "away"), (3, "home"), (4, "home"), (5, "away")]
    base_away, base_home = 3.5, 3.9
    # Congelados como si la ingesta hubiera corrido con PARK_FACTOR_WEIGHT=1.0 /
    # WEATHER_CORRECTION=0.0 (weighted_park_factor == park_factor, weather_impact == 0).
    away_proj_runs = base_away * park_factor
    home_proj_runs = base_home * park_factor + 0.15  # HOME_FIELD_RUNS_BONUS
    for game_pk, winner in games:
        session.add(historical_db.HistoricalGame(
            run_id=1, game_pk=game_pk, game_date="2024-05-01", season_year=2024,
            away_team="A", home_team="B", winner=winner,
            home_score=5 if winner == "home" else 2, away_score=2 if winner == "home" else 5,
        ))
        session.add(historical_db.HistoricalAnalysis(
            run_id=1, game_pk=game_pk, game_date="2024-05-01", season_year=2024, as_of_date="2024-04-30",
            home_proj_runs=home_proj_runs, away_proj_runs=away_proj_runs,
            park_factor=park_factor, temp_f=temp_f,
        ))
    session.commit()
    session.close()
    return Session


def test_park_weather_recalibration_never_modifies_production_config(tmp_path):
    original_park_weight = production_config.PARK_FACTOR_WEIGHT
    original_weather = production_config.WEATHER_CORRECTION

    Session = _seeded_with_park_factor(tmp_path, "park_no_mutation")
    propose_runs_projection_recalibration(
        season_year=2024, run_id=1, param_name="PARK_FACTOR_WEIGHT",
        candidate_values=[0.5, 1.5], session_factory=Session,
    )

    assert production_config.PARK_FACTOR_WEIGHT == original_park_weight
    assert production_config.WEATHER_CORRECTION == original_weather


def test_park_weather_recalibration_always_marks_applied_false(tmp_path):
    Session = _seeded_with_park_factor(tmp_path, "park_applied_false")
    result = propose_runs_projection_recalibration(
        season_year=2024, run_id=1, param_name="PARK_FACTOR_WEIGHT",
        candidate_values=[0.5, 1.5], session_factory=Session,
    )
    assert result["applied"] is False

    session = Session()
    sims = session.query(historical_db.HistoricalSimulation).all()
    session.close()
    assert len(sims) > 0
    assert all(sim.applied is False for sim in sims)


def test_park_weather_recalibration_returns_a_proposal_per_candidate_value(tmp_path):
    Session = _seeded_with_park_factor(tmp_path, "park_candidates")
    candidates = [0.5, 1.0, 1.5]
    result = propose_runs_projection_recalibration(
        season_year=2024, run_id=1, param_name="PARK_FACTOR_WEIGHT",
        candidate_values=candidates, session_factory=Session,
    )
    assert len(result["proposals"]) == 3
    assert {p["param_value"] for p in result["proposals"]} == set(candidates)


def test_park_factor_weight_recalibration_uses_real_skellam_function(tmp_path):
    # park_factor=1.10 (!= 1.0) -- si park_factor_weight no afectara el
    # cálculo, todos los candidatos darían el mismo Brier.
    Session = _seeded_with_park_factor(tmp_path, "park_real_function", park_factor=1.10)
    result = propose_runs_projection_recalibration(
        season_year=2024, run_id=1, param_name="PARK_FACTOR_WEIGHT",
        candidate_values=[0.3, 2.0], session_factory=Session,
    )
    briers = [p["brier_score"] for p in result["proposals"]]
    assert briers[0] != briers[1]


def test_weather_correction_recalibration_uses_real_skellam_function(tmp_path):
    # temp_f=90 (> 85) en todos los juegos sembrados -- weather_correction sí
    # se activa, así que candidatos distintos deben dar Brier distinto.
    Session = _seeded_with_park_factor(tmp_path, "weather_real_function", temp_f=90.0)
    result = propose_runs_projection_recalibration(
        season_year=2024, run_id=1, param_name="WEATHER_CORRECTION",
        candidate_values=[-0.05, 0.08], session_factory=Session,
    )
    briers = [p["brier_score"] for p in result["proposals"]]
    assert briers[0] != briers[1]


def test_weather_correction_recalibration_ignores_cool_games(tmp_path):
    # temp_f=70 (<= 85) -- weather_correction nunca se activa, así que
    # cualquier candidato debe dar el mismo Brier que el baseline.
    Session = _seeded_with_park_factor(tmp_path, "weather_cool_games", temp_f=70.0)
    result = propose_runs_projection_recalibration(
        season_year=2024, run_id=1, param_name="WEATHER_CORRECTION",
        candidate_values=[-0.05, 0.08], session_factory=Session,
    )
    briers = [p["brier_score"] for p in result["proposals"]]
    assert briers[0] == briers[1] == result["baseline_brier_score"]


def test_runs_projection_recalibration_rejects_unknown_param_name(tmp_path):
    Session = _seeded_with_park_factor(tmp_path, "park_invalid_param")
    try:
        propose_runs_projection_recalibration(
            season_year=2024, run_id=1, param_name="NEGBIN_DISPERSION",
            candidate_values=[1.0], session_factory=Session,
        )
        assert False, "debía lanzar ValueError para un param_name no soportado"
    except ValueError:
        pass


def test_recompute_mu_with_candidate_returns_none_without_park_factor():
    analysis = historical_db.HistoricalAnalysis(
        run_id=1, game_pk=1, game_date="2024-05-01", season_year=2024, as_of_date="2024-04-30",
        home_proj_runs=4.5, away_proj_runs=3.8, park_factor=None, temp_f=None,
    )
    home_mu, away_mu = _recompute_mu_with_candidate(analysis, park_factor_weight=1.15, weather_correction=0.05)
    assert home_mu is None
    assert away_mu is None


def test_recompute_mu_with_candidate_reproduces_baseline_exactly():
    # Con park_factor_weight=1.0 y weather_correction=0.0 (los valores con los
    # que se congeló el snapshot sembrado), la inversión debe devolver
    # EXACTAMENTE home_proj_runs/away_proj_runs originales -- es la propiedad
    # que hace segura la inversión algebraica (round-trip sin pérdida).
    park_factor = 1.10
    away_proj_runs = 3.5 * park_factor
    home_proj_runs = 3.9 * park_factor + 0.15
    analysis = historical_db.HistoricalAnalysis(
        run_id=1, game_pk=1, game_date="2024-05-01", season_year=2024, as_of_date="2024-04-30",
        home_proj_runs=home_proj_runs, away_proj_runs=away_proj_runs,
        park_factor=park_factor, temp_f=None,
    )
    home_mu, away_mu = _recompute_mu_with_candidate(analysis, park_factor_weight=1.0, weather_correction=0.0)
    assert home_mu == pytest.approx(home_proj_runs)
    assert away_mu == pytest.approx(away_proj_runs)


# --- propose_starter_weight_recalibration (STARTER_WEIGHT) ---
#
# A diferencia de los dos barridos anteriores, aquí NO se congelan
# home_proj_runs/away_proj_runs -- se siembran ERA/OPS/bullpen crudos y se
# recalcula con predict_from_raw_inputs() real. Abridor y bullpen con ERA
# bien distintos (2.50 vs 5.50) para que el peso SÍ importe.

def _seeded_with_raw_inputs(tmp_path, name):
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db")
    historical_db.HistoricalBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    games = [(1, "home"), (2, "away"), (3, "home"), (4, "home"), (5, "away"), (6, "away")]
    for game_pk, winner in games:
        session.add(historical_db.HistoricalGame(
            run_id=1, game_pk=game_pk, game_date="2024-05-01", season_year=2024,
            away_team="A", home_team="B", winner=winner,
            home_score=5 if winner == "home" else 2, away_score=2 if winner == "home" else 5,
        ))
        session.add(historical_db.HistoricalAnalysis(
            run_id=1, game_pk=game_pk, game_date="2024-05-01", season_year=2024, as_of_date="2024-04-30",
            away_era=2.50, home_era=3.00, away_bullpen_era=5.50, home_bullpen_era=5.00,
            away_ops=0.740, home_ops=0.760, park_factor=1.0, temp_f=70.0,
        ))
    session.commit()
    session.close()
    return Session


def test_starter_weight_recalibration_never_modifies_production_config(tmp_path):
    original = production_config.STARTER_WEIGHT

    Session = _seeded_with_raw_inputs(tmp_path, "starter_weight_no_mutation")
    propose_starter_weight_recalibration(
        season_year=2024, run_id=1, candidate_values=[0.3, 0.9], session_factory=Session,
    )

    assert production_config.STARTER_WEIGHT == original


def test_starter_weight_recalibration_always_marks_applied_false(tmp_path):
    Session = _seeded_with_raw_inputs(tmp_path, "starter_weight_applied_false")
    result = propose_starter_weight_recalibration(
        season_year=2024, run_id=1, candidate_values=[0.3, 0.9], session_factory=Session,
    )
    assert result["applied"] is False

    session = Session()
    sims = session.query(historical_db.HistoricalSimulation).all()
    session.close()
    assert len(sims) > 0
    assert all(sim.applied is False for sim in sims)


def test_starter_weight_recalibration_returns_a_proposal_per_candidate_value(tmp_path):
    Session = _seeded_with_raw_inputs(tmp_path, "starter_weight_candidates")
    candidates = [0.3, 0.5, 0.9]
    result = propose_starter_weight_recalibration(
        season_year=2024, run_id=1, candidate_values=candidates, session_factory=Session,
    )
    assert len(result["proposals"]) == 3
    assert {p["param_value"] for p in result["proposals"]} == set(candidates)


def test_starter_weight_recalibration_uses_real_predict_from_raw_inputs(tmp_path):
    # ERA de abridor (2.5-3.0) muy distinto del bullpen (5.0-5.5) -- si
    # STARTER_WEIGHT no afectara el cálculo, todos los candidatos darían el
    # mismo Brier.
    Session = _seeded_with_raw_inputs(tmp_path, "starter_weight_real_function")
    result = propose_starter_weight_recalibration(
        season_year=2024, run_id=1, candidate_values=[0.1, 0.95], session_factory=Session,
    )
    briers = [p["brier_score"] for p in result["proposals"]]
    assert briers[0] != briers[1]


def test_raw_inputs_for_starter_weight_candidate_returns_none_without_required_field():
    analysis = historical_db.HistoricalAnalysis(
        run_id=1, game_pk=1, game_date="2024-05-01", season_year=2024, as_of_date="2024-04-30",
        away_era=2.5, home_era=3.0, away_ops=0.740, home_ops=0.760,
        away_bullpen_era=None, home_bullpen_era=5.00, park_factor=1.0,
    )
    assert _raw_inputs_for_starter_weight_candidate(analysis, 0.65) is None


def test_raw_inputs_for_starter_weight_candidate_sets_the_candidate_weight():
    analysis = historical_db.HistoricalAnalysis(
        run_id=1, game_pk=1, game_date="2024-05-01", season_year=2024, as_of_date="2024-04-30",
        away_era=2.5, home_era=3.0, away_ops=0.740, home_ops=0.760,
        away_bullpen_era=5.50, home_bullpen_era=5.00, park_factor=1.0,
    )
    raw = _raw_inputs_for_starter_weight_candidate(analysis, 0.42)
    assert raw["starter_weight"] == 0.42


# --- propose_probability_shrinkage (calibración por contracción hacia 0.5) ---
#
# Se siembra un motor deliberadamente SOBRECONFIADO: declara 0.80/0.20 en
# juegos que en realidad se ganan ~50/50 -- la contracción (alpha < 1) debe
# mejorar el Brier, y alpha == 1.0 debe reproducir el baseline exacto.

def _seeded_with_overconfident_predictions(tmp_path, name, source="skellam"):
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db")
    historical_db.HistoricalBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    # 4 juegos: el motor declara 0.80 para el local en todos, pero el local
    # gana solo 2 de 4 -- sobreconfianza pura.
    winners = ["home", "away", "home", "away"]
    for game_pk, winner in enumerate(winners, start=1):
        session.add(historical_db.HistoricalPrediction(
            run_id=1, game_pk=game_pk, game_date="2024-05-01", season_year=2024,
            source=source, home_prob=0.80, away_prob=0.20,
            predicted_winner="home", actual_winner=winner, correct=(winner == "home"),
        ))
    session.commit()
    session.close()
    return Session


def test_shrinkage_never_modifies_production_and_marks_applied_false(tmp_path):
    Session = _seeded_with_overconfident_predictions(tmp_path, "shrink_applied_false")
    result = propose_probability_shrinkage(
        source="skellam", season_year=2024, run_id=1, candidate_values=[0.5, 1.0], session_factory=Session,
    )
    assert result["applied"] is False

    session = Session()
    sims = session.query(historical_db.HistoricalSimulation).all()
    session.close()
    assert len(sims) > 0
    assert all(sim.applied is False for sim in sims)
    assert all(sim.param_name == "PROB_SHRINKAGE_SKELLAM" for sim in sims)


def test_shrinkage_improves_brier_for_overconfident_model(tmp_path):
    Session = _seeded_with_overconfident_predictions(tmp_path, "shrink_improves")
    result = propose_probability_shrinkage(
        source="skellam", season_year=2024, run_id=1,
        candidate_values=[0.0, 0.5, 1.0], session_factory=Session,
    )
    by_alpha = {p["param_value"]: p for p in result["proposals"]}
    # alpha=1.0 es la identidad: mismo Brier que el baseline.
    assert by_alpha[1.0]["brier_score"] == pytest.approx(result["baseline_brier_score"])
    # Con aciertos 50/50 y probs 0.80, encoger SIEMPRE mejora -- y alpha=0.0
    # (todo a 0.5) es el óptimo teórico para esta semilla.
    assert by_alpha[0.5]["improved_over_baseline"] is True
    assert result["best_candidate"]["param_value"] == 0.0


def test_shrinkage_only_uses_predictions_of_the_requested_source(tmp_path):
    Session = _seeded_with_overconfident_predictions(tmp_path, "shrink_source_filter", source="negbin")
    result = propose_probability_shrinkage(
        source="skellam", season_year=2024, run_id=1, candidate_values=[0.5], session_factory=Session,
    )
    # No hay filas de skellam sembradas -- n debe ser 0 y ningún Brier calculable.
    assert result["baseline_n_sample"] == 0
    assert result["baseline_brier_score"] is None
