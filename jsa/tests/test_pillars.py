import datetime

from jsa.domain.models import build_game_snapshot
from jsa.engine.context_detector import detect_context
from jsa.engine.pillars import evaluate_all_pillars
from jsa.domain.models import SEVEN_PILLARS


def _snap(**overrides):
    fields = dict(game_id="g1", game_date=datetime.date(2026, 7, 13), season=2026, home_team="NYY", away_team="BOS")
    fields.update(overrides)
    return build_game_snapshot(**fields)


def test_all_seven_pillars_returned_in_order():
    snap = _snap()
    ctx = detect_context(snap)
    pillars = evaluate_all_pillars(snap, ctx)
    assert [p.pillar for p in pillars] == list(SEVEN_PILLARS)


def test_advantages_are_discrete_and_in_range():
    snap = _snap(
        home_starter_xera=2.0, away_starter_xera=6.0, home_starter_ip_sample=150, away_starter_ip_sample=150,
        home_ops=0.900, away_ops=0.600, home_ops_pa_sample=400, away_ops_pa_sample=400,
        league_avg_era=4.3, league_avg_ops=0.75,
    )
    ctx = detect_context(snap)
    pillars = evaluate_all_pillars(snap, ctx)
    for p in pillars:
        assert p.advantage in (-2, -1, 0, 1, 2)


def test_pillar_contract_version_present_and_versioned():
    snap = _snap()
    ctx = detect_context(snap)
    pillars = evaluate_all_pillars(snap, ctx)
    for p in pillars:
        assert "@" in p.pillar_contract_version


def test_starter_pillar_no_data_is_neutral():
    snap = _snap()  # sin xera para ninguno de los dos
    ctx = detect_context(snap)
    pillars = evaluate_all_pillars(snap, ctx)
    starter = next(p for p in pillars if p.pillar == "starter")
    assert starter.advantage == 0
    assert "sin datos" in starter.explanation.lower()


def test_pillars_are_pure_functions_of_snapshot_and_context():
    """Misma senal de entrada -> misma salida, siempre (precondicion para
    que evaluate_game() sea la unica funcion de recalculo punto-en-el-
    tiempo, Seccion 2)."""
    snap = _snap(home_starter_xera=3.0, away_starter_xera=4.0, home_starter_ip_sample=100, away_starter_ip_sample=100)
    ctx = detect_context(snap)
    first = evaluate_all_pillars(snap, ctx)
    second = evaluate_all_pillars(snap, ctx)
    assert [p.model_dump() for p in first] == [p.model_dump() for p in second]
