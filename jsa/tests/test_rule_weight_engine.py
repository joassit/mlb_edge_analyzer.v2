import datetime

from jsa.config import BASE_PILLAR_WEIGHTS
from jsa.domain.models import RuleDefinition, build_game_snapshot
from jsa.engine.context_detector import detect_context
from jsa.engine.rule_engine import accumulate_deltas, evaluate_rules, rules_applied_per_pillar
from jsa.engine.weight_engine import apply_weights


def _snap(**overrides):
    fields = dict(game_id="g1", game_date=datetime.date(2026, 7, 13), season=2026, home_team="NYY", away_team="BOS")
    fields.update(overrides)
    return build_game_snapshot(**fields)


def _active_registry_with_experiment(rule_id: str, experiment_id: str) -> dict[str, RuleDefinition]:
    from jsa.engine.rule_definitions import RULE_SPECS_BY_ID

    spec = RULE_SPECS_BY_ID[rule_id]
    return {
        rule_id: RuleDefinition(
            rule_id=rule_id, trigger=spec.trigger_signal, condition=spec.condition,
            weight_adjustments=spec.weight_adjustments, scientific_justification=spec.scientific_justification,
            version="1.0", status="active", experiments_supporting_rule=[experiment_id],
        )
    }


def test_experimental_rules_never_move_production_weights():
    snap = _snap(home_starter_projected_ip=6.8, away_starter_projected_ip=6.6)
    ctx = detect_context(snap)
    trace = evaluate_rules(snap, ctx, rule_registry={}, experiment_ids=set())
    assert trace  # long_outing triggered
    assert all(not e.applied_to_weights for e in trace)

    deltas = accumulate_deltas(trace)
    assert deltas == {}

    final_weights, _ = apply_weights(BASE_PILLAR_WEIGHTS, deltas, rules_applied_per_pillar(trace))
    assert final_weights.as_dict() == BASE_PILLAR_WEIGHTS


def test_active_rule_with_valid_experiment_applies_to_weights():
    snap = _snap(home_starter_projected_ip=6.8, away_starter_projected_ip=6.6)
    ctx = detect_context(snap)
    registry = _active_registry_with_experiment("long_outing", "exp-001")
    trace = evaluate_rules(snap, ctx, rule_registry=registry, experiment_ids={"exp-001"})

    applied = [e for e in trace if e.rule_id == "long_outing"]
    assert applied and applied[0].applied_to_weights is True

    deltas = accumulate_deltas(trace)
    assert deltas["starter"] == 0.06
    assert deltas["bullpen"] == -0.06


def test_active_rule_without_valid_experiment_does_not_apply():
    snap = _snap(home_starter_projected_ip=6.8, away_starter_projected_ip=6.6)
    ctx = detect_context(snap)
    registry = _active_registry_with_experiment("long_outing", "exp-001")
    # exp-001 no existe en el Experiment Registry -> no puede aplicarse (Seccion 6.6)
    trace = evaluate_rules(snap, ctx, rule_registry=registry, experiment_ids=set())
    applied = [e for e in trace if e.rule_id == "long_outing"]
    assert applied[0].applied_to_weights is False


def test_rule_engine_commutative():
    """Seccion 6.4 regla 3: A luego B da el mismo resultado que B luego A
    -- se prueba aplicando los deltas en ambos ordenes de iteracion."""
    deltas_ab = {"starter": 0.06, "bullpen": -0.06}
    deltas_ba = {"bullpen": -0.06, "starter": 0.06}
    weights_ab, _ = apply_weights(BASE_PILLAR_WEIGHTS, deltas_ab, {})
    weights_ba, _ = apply_weights(BASE_PILLAR_WEIGHTS, deltas_ba, {})
    assert weights_ab.as_dict() == weights_ba.as_dict()


def test_negative_weight_truncated_to_zero():
    deltas = {"trend": -0.20}  # base trend=0.05, delta lo dejaria negativo
    weights, audit = apply_weights(BASE_PILLAR_WEIGHTS, deltas, {})
    assert weights.trend == 0.0
    trend_entry = next(e for e in audit if e.pillar == "trend")
    assert trend_entry.weight_before_renorm == 0.0


def test_weight_audit_entries_are_reconstructible():
    deltas = {"starter": 0.06, "bullpen": -0.06}
    _, audit = apply_weights(BASE_PILLAR_WEIGHTS, deltas, {"starter": ["long_outing"], "bullpen": ["long_outing"]})
    for entry in audit:
        assert entry.verify()


def test_final_weights_always_sum_to_one():
    deltas = {"starter": 0.06, "bullpen": -0.06, "offense": -0.05, "team_quality": 0.05}
    weights, _ = apply_weights(BASE_PILLAR_WEIGHTS, deltas, {})
    assert abs(sum(weights.as_dict().values()) - 1.0) < 1e-9
