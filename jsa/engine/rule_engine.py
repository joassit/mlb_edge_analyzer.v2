"""Rule Engine -- Secciones 6.2-6.4 y 6.6 del spec JSA v3.0.

Solo modifica pesos, nunca advantages ni metricas crudas (Seccion 2). Las
reglas activadas se evaluan de forma independiente contra los pesos base y
sus ajustes se suman algebraicamente antes de renormalizar (Seccion 6.4) --
el orden de aplicacion nunca importa (conmutatividad).

Regla de negocio central de esta entrega (Seccion 6.6, ver plan): una
regla en estado `experimental` (o `active` sin `supporting_experiment_id`
vigente en el Experiment Registry) se calcula y se deja en el Rule Trace,
pero **no** entra a la suma algebraica de ajustes de peso -- el sistema es
nuevo y ninguna de las 6 reglas heredadas tiene todavia un experimento de
respaldo real."""

from __future__ import annotations

from datetime import datetime, timezone

from jsa.domain.hashing import hash_value
from jsa.domain.models import ContextSignals, GameSnapshot, RuleDefinition, RuleTraceEntry
from jsa.engine.rule_definitions import RULE_SPECS


def evaluate_rules(
    snapshot: GameSnapshot,
    context: ContextSignals,
    rule_registry: dict[str, RuleDefinition],
    experiment_ids: set[str],
) -> list[RuleTraceEntry]:
    """Para cada regla en `RULE_SPECS`, evalua si su trigger disparo en
    `context` y si puede aplicarse en produccion segun el Rule Registry
    (`rule_registry`, `rule_id -> RuleDefinition`) y el Experiment Registry
    (`experiment_ids`, IDs de experimentos existentes)."""
    trace: list[RuleTraceEntry] = []
    now = datetime.now(timezone.utc).isoformat()

    for spec in RULE_SPECS:
        triggered = getattr(context, spec.trigger_signal, False)
        if not triggered:
            continue

        definition = rule_registry.get(spec.rule_id)
        status = definition.status if definition else "experimental"
        supporting_id = definition.experiments_supporting_rule[0] if definition and definition.experiments_supporting_rule else None
        has_valid_experiment = supporting_id is not None and supporting_id in experiment_ids

        # Seccion 6.6, regla dura: solo aplica en produccion si status=="active"
        # Y tiene un supporting_experiment_id vigente. Cualquier otro caso se
        # traza igual (visibilidad total) pero no mueve pesos.
        applied = status == "active" and has_valid_experiment

        condition_inputs = {f: getattr(snapshot, f) for f in spec.snapshot_fields}
        # list[str] no es JSON-serializable de forma estable en un dict simple
        # sin normalizar -- hash_value ya lo hace via domain.hashing.
        input_hash = hash_value(condition_inputs)

        justification = definition.scientific_justification if definition else spec.scientific_justification

        trace.append(
            RuleTraceEntry(
                rule_id=spec.rule_id,
                trigger_signal=spec.trigger_signal,
                input_data_hash=input_hash,
                supporting_experiment_id=supporting_id if has_valid_experiment else None,
                scientific_justification=justification,
                game_id=snapshot.game_id,
                timestamp=now,
                applied_to_weights=applied,
            )
        )

    return trace


def accumulate_deltas(trace: list[RuleTraceEntry]) -> dict[str, float]:
    """Suma algebraica de los ajustes de peso de las reglas con
    `applied_to_weights=True`. Conmutativo por construccion: es una suma,
    el orden de iteracion de `trace` no afecta el resultado."""
    deltas: dict[str, float] = {}
    applied_ids = {e.rule_id for e in trace if e.applied_to_weights}
    for spec in RULE_SPECS:
        if spec.rule_id not in applied_ids:
            continue
        for pillar, delta in spec.weight_adjustments.items():
            deltas[pillar] = deltas.get(pillar, 0.0) + delta
    return deltas


def rules_applied_per_pillar(trace: list[RuleTraceEntry]) -> dict[str, list[str]]:
    """rule_id's que SI aplicaron, agrupados por pilar que ajustan --
    alimenta `WeightAuditEntry.rules_applied`."""
    applied_ids = {e.rule_id for e in trace if e.applied_to_weights}
    result: dict[str, list[str]] = {}
    for spec in RULE_SPECS:
        if spec.rule_id not in applied_ids:
            continue
        for pillar in spec.weight_adjustments:
            result.setdefault(pillar, []).append(spec.rule_id)
    return result


def human_readable_summary(trace: list[RuleTraceEntry]) -> list[str]:
    lines = []
    for entry in trace:
        spec = next((s for s in RULE_SPECS if s.rule_id == entry.rule_id), None)
        adj_desc = ""
        if spec:
            adj_desc = ", ".join(f"{p} {'+' if d >= 0 else ''}{d:.2f}" for p, d in spec.weight_adjustments.items())
        status = "aplicada" if entry.applied_to_weights else "detectada pero NO aplicada (experimental / sin experimento de respaldo)"
        lines.append(f"{entry.rule_id} [{status}]: {adj_desc}")
    return lines
