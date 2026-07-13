"""Especificacion canonica de las 6 reglas heredadas -- Seccion 6.3 del
spec JSA v3.0.

Unico lugar donde vive el `rule_id`, el trigger, los deltas de peso y la
justificacion cientifica de cada regla -- tanto `engine/rule_engine.py`
(que las evalua en cada corrida) como `registries/seed.py` (que siembra el
Rule Registry) importan de aqui, para que ambos nunca puedan desincronizarse
entre si."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    trigger_signal: str  # atributo de ContextSignals
    condition: str  # expresion exacta, legible por humano
    weight_adjustments: dict[str, float]
    scientific_justification: str
    snapshot_fields: tuple[str, ...]  # campos de GameSnapshot que la condicion evalua


RULE_SPECS: tuple[RuleSpec, ...] = (
    RuleSpec(
        rule_id="long_outing",
        trigger_signal="long_outing",
        condition="avg_projected_ip >= 6.5",
        weight_adjustments={"starter": 0.06, "bullpen": -0.06},
        scientific_justification=(
            "Heredada de mlb_edge_analyzer.v2 como hipotesis sabermetrica de partida "
            "(un abridor que proyecta salida profunda reduce la exposicion real del "
            "bullpen en el resultado del juego); pendiente de experimento de validacion "
            "propio de JSA -- ver Rule Registry, status=experimental."
        ),
        snapshot_fields=("home_starter_projected_ip", "away_starter_projected_ip"),
    ),
    RuleSpec(
        rule_id="short_outing_bullpen_game",
        trigger_signal="short_outing_bullpen_game",
        condition="avg_projected_ip <= 5.0",
        weight_adjustments={"bullpen": 0.10, "starter": -0.10},
        scientific_justification=(
            "Hipotesis simetrica a long_outing: salida corta esperada aumenta la "
            "exposicion real del bullpen. Pendiente de experimento de validacion propio."
        ),
        snapshot_fields=("home_starter_projected_ip", "away_starter_projected_ip"),
    ),
    RuleSpec(
        rule_id="key_offensive_injuries",
        trigger_signal="key_offensive_injuries",
        condition="len(home_key_injuries) + len(away_key_injuries) >= 2",
        weight_adjustments={"offense": -0.05, "team_quality": 0.05},
        scientific_justification=(
            "Con 2+ lesiones ofensivas clave combinadas, el OPS de temporada deja de "
            "reflejar el lineup real que sale a jugar -- se redistribuye peso hacia "
            "team_quality como senal mas estable. Pendiente de experimento propio."
        ),
        snapshot_fields=("home_key_injuries", "away_key_injuries"),
    ),
    RuleSpec(
        rule_id="double_header",
        trigger_signal="double_header",
        condition="is_double_header == True",
        weight_adjustments={"bullpen": 0.05, "context": 0.03, "starter": -0.04},
        scientific_justification=(
            "Doble cartelera tipicamente fuerza manejo de bullpen distinto al usual y "
            "un abridor con menos margen de continuar si va mal. Pendiente de "
            "experimento propio."
        ),
        snapshot_fields=("is_double_header",),
    ),
    RuleSpec(
        rule_id="extreme_travel",
        trigger_signal="extreme_travel",
        condition="travel_distance > 2000",
        weight_adjustments={"context": 0.04, "trend": -0.02},
        scientific_justification=(
            "Viaje extremo (jet lag, cambio de zona horaria) es una hipotesis de "
            "fatiga con soporte en literatura deportiva general, no validada todavia "
            "con datos propios de MLB/JSA."
        ),
        snapshot_fields=("travel_distance",),
    ),
    RuleSpec(
        rule_id="bullpen_fatigue",
        trigger_signal="bullpen_fatigue",
        condition="bullpen_ip_last_3_days > 10",
        weight_adjustments={"bullpen": 0.05, "team_quality": -0.02},
        scientific_justification=(
            "Bullpen sobreusado en los ultimos 3 dias es una senal directa de "
            "disponibilidad reducida de brazos frescos. Pendiente de experimento propio "
            "(y de que la fuente de datos de bullpen_ip_last_3_days este wireada -- ver "
            "ROADMAP)."
        ),
        snapshot_fields=("home_bullpen_ip_last_3_days", "away_bullpen_ip_last_3_days"),
    ),
)

RULE_SPECS_BY_ID: dict[str, RuleSpec] = {r.rule_id: r for r in RULE_SPECS}
