"""Weight Engine -- Secciones 6.4-6.5 del spec JSA v3.0.

Aplica los ajustes acumulativos que ya vienen sumados algebraicamente
(`rule_engine.accumulate_deltas`) y renormaliza a suma=1.0. No evalua
evidencia -- solo pesos. Devuelve tanto los `PillarWeights` finales como la
tabla estructurada `WeightAuditEntry` por pilar (Seccion 6.5), reconstruible
al 1e-9."""

from __future__ import annotations

from jsa.domain.models import SEVEN_PILLARS, PillarWeights, WeightAuditEntry


def apply_weights(
    base_weights: dict[str, float],
    deltas: dict[str, float],
    rules_applied_per_pillar: dict[str, list[str]],
) -> tuple[PillarWeights, list[WeightAuditEntry]]:
    weight_before_renorm: dict[str, float] = {}
    for pillar in SEVEN_PILLARS:
        base = base_weights[pillar]
        delta = deltas.get(pillar, 0.0)
        raw = base + delta
        # Seccion 6.4, regla 4: si la suma deja un peso negativo, se trunca a 0.
        weight_before_renorm[pillar] = max(0.0, raw)

    total_actual = sum(weight_before_renorm.values())
    renorm_factor = (1.0 / total_actual) if total_actual > 0 else 0.0

    entries: list[WeightAuditEntry] = []
    final_weights: dict[str, float] = {}
    for pillar in SEVEN_PILLARS:
        base = base_weights[pillar]
        delta = deltas.get(pillar, 0.0)
        before = weight_before_renorm[pillar]
        final = before * renorm_factor
        final_weights[pillar] = final

        applied_rules = rules_applied_per_pillar.get(pillar, [])
        if applied_rules:
            human = (
                f"Pesos aplicados: {', '.join(applied_rules)} -> delta neto {delta:+.2f} sobre base "
                f"{base:.2f}, renormalizado x{renorm_factor:.4f} = {final:.4f}."
            )
        else:
            human = f"Sin reglas aplicadas -- base {base:.2f} renormalizado x{renorm_factor:.4f} = {final:.4f}."

        entries.append(
            WeightAuditEntry(
                pillar=pillar,
                base_weight=base,
                rules_applied=applied_rules,
                delta_total=delta,
                weight_before_renorm=before,
                renormalization_factor=renorm_factor,
                final_weight=final,
                human_explanation=human,
            )
        )

    return PillarWeights(**final_weights), entries
