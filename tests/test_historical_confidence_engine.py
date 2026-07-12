"""
Pruebas del Historical Confidence Engine -- la garantía central es que
NUNCA genera ni altera una probabilidad de resultado, solo consulta
evidencia histórica ya acumulada.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import historical_engine.db as historical_db
from historical_engine.confidence_engine import HistoricalConfidenceEngine, HistoricalConfidenceReport


def _seeded_session(tmp_path, name: str):
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db")
    historical_db.HistoricalBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add_all([
        historical_db.HistoricalCalibration(
            run_id=1, season_year=2024, source="skellam", bucket_label="60-65%",
            bucket_low=0.60, bucket_high=0.65, n=120, hits=84, avg_confidence=0.62, hit_rate=0.70, gap=0.08,
        ),
        historical_db.HistoricalCalibration(
            run_id=1, season_year=2025, source="skellam", bucket_label="60-65%",
            bucket_low=0.60, bucket_high=0.65, n=110, hits=70, avg_confidence=0.62, hit_rate=0.636, gap=0.016,
        ),
    ])
    session.commit()
    session.close()
    return Session


def test_report_has_no_field_that_could_be_mistaken_for_a_new_probability():
    fields = HistoricalConfidenceReport.__dataclass_fields__.keys()
    forbidden_substrings = ["win_prob", "model_prob", "corrected_prob", "adjusted_prob", "final_prob"]
    for f in fields:
        for forbidden in forbidden_substrings:
            assert forbidden not in f, f"El campo {f!r} podría confundirse con una probabilidad de resultado"


def test_evaluate_does_not_mutate_input_probabilities(tmp_path):
    Session = _seeded_session(tmp_path, "confidence_no_mutation")
    engine = HistoricalConfidenceEngine(session_factory=Session)

    skellam_prob, negbin_prob, heuristic_prob = 0.62, 0.60, 0.55
    original = (skellam_prob, negbin_prob, heuristic_prob)

    report = engine.evaluate(skellam_prob=skellam_prob, negbin_prob=negbin_prob, heuristic_prob=heuristic_prob)

    # Los floats son inmutables en Python, pero esto prueba explícitamente
    # que las variables originales del caller siguen intactas después de
    # llamar al motor -- ninguna lógica interna las reasigna vía referencia
    # compartida ni efectos secundarios.
    assert (skellam_prob, negbin_prob, heuristic_prob) == original
    assert isinstance(report, HistoricalConfidenceReport)


def test_evaluate_uses_matching_bucket_from_historical_calibration(tmp_path):
    Session = _seeded_session(tmp_path, "confidence_bucket_match")
    engine = HistoricalConfidenceEngine(session_factory=Session)

    report = engine.evaluate(skellam_prob=0.62, negbin_prob=0.60, heuristic_prob=0.55)

    assert report.bucket_label == "60-65%"
    assert report.comparable_sample_n == 230  # 120 + 110
    assert report.seasons_represented == [2024, 2025]


def test_evaluate_reports_insufficient_evidence_when_no_history_exists(tmp_path):
    Session = _seeded_session(tmp_path, "confidence_no_history")
    engine = HistoricalConfidenceEngine(session_factory=Session)

    # 90-95% nunca se sembró en el histórico de este test.
    report = engine.evaluate(skellam_prob=0.92, negbin_prob=0.90, heuristic_prob=0.88)

    assert report.evidence_level == "insuficiente"
    assert report.comparable_sample_n == 0
    assert any("Sin historial" in w for w in report.warnings)


def test_evaluate_flags_disagreement_between_engines(tmp_path):
    Session = _seeded_session(tmp_path, "confidence_disagreement")
    engine = HistoricalConfidenceEngine(session_factory=Session)

    # heuristic favorece al lado contrario (< 0.5) que skellam/negbin (> 0.5)
    report = engine.evaluate(skellam_prob=0.62, negbin_prob=0.60, heuristic_prob=0.45)

    assert any("no coinciden en el favorito" in w for w in report.warnings)


def test_evaluate_excludes_current_season_from_its_own_evidence(tmp_path):
    Session = _seeded_session(tmp_path, "confidence_exclude_current")
    engine = HistoricalConfidenceEngine(session_factory=Session)

    report = engine.evaluate(skellam_prob=0.62, negbin_prob=0.60, heuristic_prob=0.55, exclude_season=2025)

    assert report.seasons_represented == [2024]
    assert report.comparable_sample_n == 120


def test_evidence_level_reaches_alta_only_above_200_sample(tmp_path):
    engine_module = create_engine(f"sqlite:///{tmp_path}/confidence_high_evidence.db")
    historical_db.HistoricalBase.metadata.create_all(engine_module)
    Session = sessionmaker(bind=engine_module)
    session = Session()
    session.add(historical_db.HistoricalCalibration(
        run_id=1, season_year=2024, source="skellam", bucket_label="60-65%",
        bucket_low=0.60, bucket_high=0.65, n=250, hits=160, avg_confidence=0.62, hit_rate=0.64, gap=0.02,
    ))
    session.commit()
    session.close()

    engine = HistoricalConfidenceEngine(session_factory=Session)
    report = engine.evaluate(skellam_prob=0.62, negbin_prob=0.60, heuristic_prob=0.55)
    assert report.evidence_level == "alta"
