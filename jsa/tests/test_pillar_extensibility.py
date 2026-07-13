"""Prueba el mecanismo de Pilar Experimental -- Seccion 7.3 del spec JSA
v3.0 -- con un pilar ficticio (`clutch_index`) que NUNCA se usa en
produccion real (no vive en `engine/pillars/`, no aparece en
`engine.pillars.evaluate_all_pillars`). Vive solo aqui, como demostracion
de que agregar un pilar nuevo no puede mover el Evidence Score ya
calibrado sin pasar primero por el Scientific Validation Pipeline."""

from __future__ import annotations

import datetime

import pytest

from jsa.config import BASE_PILLAR_WEIGHTS
from jsa.domain.models import PillarAdvantage, PillarWeights, build_game_snapshot
from jsa.engine.evidence_engine import compute_evidence_score, compute_feature_contribution
from jsa.engine.pillars import evaluate_all_pillars
from jsa.engine.context_detector import detect_context
from jsa.registries import db, seed


def _example_experimental_pillar() -> PillarAdvantage:
    """Evaluador de ejemplo de un pilar nuevo -- deliberadamente fuera de
    `engine/pillars/`: si viviera ahi, se veria tentador enchufarlo
    directo a `evaluate_all_pillars()`, que es exactamente lo que la
    Seccion 7.3 prohibe sin graduacion."""
    return PillarAdvantage(
        pillar="clutch_index", advantage=2, explanation="pilar experimental de demostracion",
        pillar_contract_version="clutch_index@0.1.0-experimental",
    )


def _snap():
    return build_game_snapshot(game_id="g1", game_date=datetime.date(2026, 7, 13), season=2026, home_team="NYY", away_team="BOS")


def test_experimental_pillar_is_computed_and_visible():
    """(a) El pilar experimental SI se calcula -- puede vivir junto a los
    7 activos en una lista que alimentaria JSAReport.pillar_advantages."""
    snap = _snap()
    ctx = detect_context(snap)
    base_pillars = evaluate_all_pillars(snap, ctx)
    experimental = _example_experimental_pillar()
    all_pillars_for_report = base_pillars + [experimental]

    assert "clutch_index" in [p.pillar for p in all_pillars_for_report]
    assert len(all_pillars_for_report) == 8


def test_experimental_pillar_never_enters_evidence_score():
    """(b) Su contribucion NO entra a la suma del Evidence Score, aunque
    este presente en la lista de pillar advantages -- compute_evidence_score
    solo suma sobre SEVEN_PILLARS, nunca sobre lo que traiga la lista."""
    snap = _snap()
    ctx = detect_context(snap)
    base_pillars = evaluate_all_pillars(snap, ctx)
    weights = PillarWeights(**BASE_PILLAR_WEIGHTS)

    score_without_experimental, _ = compute_evidence_score(base_pillars, weights)
    score_with_experimental, _ = compute_evidence_score(base_pillars + [_example_experimental_pillar()], weights)

    assert score_without_experimental == score_with_experimental


def test_experimental_pillar_breaks_dominance_detector_if_misused():
    """(d) Si algo intenta pasar el pilar experimental a
    compute_feature_contribution() (que alimenta el Dominance Detector y,
    via ese, el Confidence Gate) como si fuera uno de los 7 activos, el
    sistema levanta un error explicito -- nunca un calculo silencioso con
    un peso que no existe para ese pilar."""
    snap = _snap()
    ctx = detect_context(snap)
    base_pillars = evaluate_all_pillars(snap, ctx)
    weights = PillarWeights(**BASE_PILLAR_WEIGHTS)

    with pytest.raises(KeyError):
        compute_feature_contribution(base_pillars + [_example_experimental_pillar()], weights)


def test_dominance_detector_and_gate_ignore_experimental_pillar_when_used_correctly():
    """(c) Usado correctamente (solo los 7 activos entran a
    compute_feature_contribution), el pilar experimental no puede disparar
    ni un dominance_warning ni bloquear/pasar el Confidence Gate -- porque
    simplemente nunca llega a esa funcion."""
    snap = _snap()
    ctx = detect_context(snap)
    base_pillars = evaluate_all_pillars(snap, ctx)
    weights = PillarWeights(**BASE_PILLAR_WEIGHTS)

    contributions = compute_feature_contribution(base_pillars, weights)
    assert all(c.pillar != "clutch_index" for c in contributions)


def test_pillar_registry_isolates_experimental_pillar_from_active_set():
    engine = db.get_engine("sqlite:///:memory:")
    seed.seed_all(engine)
    seed.seed_example_experimental_pillar(engine)

    pillars = db.latest_by_id(engine, db.pillar_registry, "pillar_id")
    assert pillars["clutch_index"]["status"] == "experimental"

    from jsa.domain.models import SEVEN_PILLARS

    for p in SEVEN_PILLARS:
        assert pillars[p]["status"] == "active"
    assert "clutch_index" not in SEVEN_PILLARS
