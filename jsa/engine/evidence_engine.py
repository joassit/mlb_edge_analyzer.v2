"""Evidence Engine -- Secciones 8.1-8.3, 8.5 y 7.2 del spec JSA v3.0.

Calcula scores y construye los insumos del reporte final. No detecta
hechos ni aplica reglas (Seccion 2) -- consume `PillarAdvantage`s y
`PillarWeights` ya resueltos por capas anteriores."""

from __future__ import annotations

from jsa import config
from jsa.domain.models import (
    SEVEN_PILLARS,
    ContextSignals,
    FeatureContributionEntry,
    GameSnapshot,
    MathAudit,
    MathAuditTerm,
    PillarAdvantage,
    PillarWeights,
)


def compute_evidence_score(pillars: list[PillarAdvantage], weights: PillarWeights) -> tuple[float, MathAudit]:
    """Seccion 8.1: Evidence Score = Sum(final_weight_pilar * advantage_pilar).
    Seccion 8.5: el desarrollo se genera programaticamente a partir de los
    MISMOS valores del calculo real, nunca reconstruido aproximadamente."""
    weights_by_pillar = weights.as_dict()
    advantage_by_pillar = {p.pillar: p.advantage for p in pillars}

    terms = []
    for pillar in SEVEN_PILLARS:
        w = weights_by_pillar[pillar]
        adv = advantage_by_pillar.get(pillar, 0)
        terms.append(MathAuditTerm(label=pillar, weight=w, value=float(adv), product=w * adv))

    total = sum(t.product for t in terms)
    audit = MathAudit(formula_name="Evidence Score", terms=terms, total=total)
    return total, audit


def compute_feature_contribution(pillars: list[PillarAdvantage], weights: PillarWeights) -> list[FeatureContributionEntry]:
    """Seccion 7.2: contribucion absoluta/porcentual por pilar + Dominance
    Detector (>40% del Evidence Score dispara `dominance_warning`)."""
    weights_by_pillar = weights.as_dict()
    absolute: dict[str, float] = {}
    for p in pillars:
        absolute[p.pillar] = weights_by_pillar[p.pillar] * p.advantage

    total_abs = sum(abs(v) for v in absolute.values())
    entries = []
    for p in pillars:
        abs_contrib = absolute[p.pillar]
        pct = (abs(abs_contrib) / total_abs) if total_abs > 0 else 0.0
        entries.append(
            FeatureContributionEntry(
                pillar=p.pillar,
                final_weight=weights_by_pillar[p.pillar],
                advantage=p.advantage,
                absolute_contribution=abs_contrib,
                percentage_contribution=pct,
                dominance_warning=pct > config.GATE_DOMINANCE_THRESHOLD,
            )
        )
    return entries


def compute_cri(snapshot: GameSnapshot) -> tuple[int, MathAudit, str]:
    """Seccion 8.2 -- Data Reliability Score, formula exacta."""
    xera_available = snapshot.home_starter_xera is not None and snapshot.away_starter_xera is not None
    xfip_available = snapshot.home_starter_xfip is not None and snapshot.away_starter_xfip is not None
    missing_projected_ip = snapshot.home_starter_projected_ip is None or snapshot.away_starter_projected_ip is None

    components = [
        ("starters_confirmed", config.CRI_COMPONENTS["starters_confirmed"], snapshot.starters_confirmed),
        ("lineups_official", config.CRI_COMPONENTS["lineups_official"], snapshot.lineups_official),
        ("bullpen_usage_known", config.CRI_COMPONENTS["bullpen_usage_known"], snapshot.bullpen_usage_known),
        ("no_last_minute_changes", config.CRI_COMPONENTS["no_last_minute_changes"], snapshot.no_last_minute_changes),
        ("xera_available", config.CRI_COMPONENTS["xera_available"], xera_available),
        ("xfip_available", config.CRI_COMPONENTS["xfip_available"], xfip_available),
        ("missing_projected_ip", config.CRI_COMPONENTS["missing_projected_ip"], missing_projected_ip),
    ]

    terms = [MathAuditTerm(label=name, weight=points, value=1.0 if is_true else 0.0, product=points if is_true else 0.0) for name, points, is_true in components]
    raw_total = sum(t.product for t in terms)
    audit = MathAudit(formula_name="CRI (Data Reliability Score)", terms=terms, total=raw_total)

    clipped = max(0, min(100, int(round(raw_total))))
    effective_base = f"{len(components)}/{len(components)} componentes evaluados (ninguno excluido por dato no evaluable)"
    return clipped, audit, effective_base


def apply_consistency_penalty(cri_score: int, consistency_flag: str | None) -> int:
    """Seccion 9.3: penalizacion de 10 puntos al CRI si la senal de
    Carreras Proyectadas contradice el signo del Evidence Score."""
    if consistency_flag == "conflicting":
        return max(0, cri_score - config.CONSISTENCY_CRI_PENALTY)
    return cri_score


def compute_uncertainty_index(snapshot: GameSnapshot, context: ContextSignals) -> tuple[int, MathAudit]:
    """Seccion 8.3 -- Game Uncertainty Index, formula exacta."""
    terms = [MathAuditTerm(label="base", weight=1.0, value=float(config.UNCERTAINTY_BASE), product=float(config.UNCERTAINTY_BASE))]

    fatigued_teams = sum(
        1
        for v in (snapshot.home_bullpen_ip_last_3_days, snapshot.away_bullpen_ip_last_3_days)
        if v is not None and v > config.BULLPEN_FATIGUE_IP_3D
    )
    if fatigued_teams:
        product = config.UNCERTAINTY_BULLPEN_FATIGUE * fatigued_teams
        terms.append(MathAuditTerm(label="bullpen_fatigue", weight=config.UNCERTAINTY_BULLPEN_FATIGUE, value=float(fatigued_teams), product=product))

    if context.extreme_weather:
        terms.append(MathAuditTerm(label="extreme_weather", weight=config.UNCERTAINTY_EXTREME_WEATHER, value=1.0, product=float(config.UNCERTAINTY_EXTREME_WEATHER)))

    if snapshot.is_double_header:
        terms.append(MathAuditTerm(label="double_header", weight=config.UNCERTAINTY_DOUBLE_HEADER, value=1.0, product=float(config.UNCERTAINTY_DOUBLE_HEADER)))

    if snapshot.travel_distance is not None and snapshot.travel_distance > config.UNCERTAINTY_EXTREME_TRAVEL_MILES:
        terms.append(MathAuditTerm(label="extreme_travel", weight=config.UNCERTAINTY_EXTREME_TRAVEL, value=1.0, product=float(config.UNCERTAINTY_EXTREME_TRAVEL)))

    total_injuries = len(snapshot.home_key_injuries) + len(snapshot.away_key_injuries)
    if total_injuries:
        injury_points = min(config.UNCERTAINTY_INJURY_CAP, config.UNCERTAINTY_PER_INJURY * total_injuries)
        terms.append(MathAuditTerm(label="key_injuries", weight=config.UNCERTAINTY_PER_INJURY, value=float(total_injuries), product=float(injury_points)))

    raw_total = sum(t.product for t in terms)
    audit = MathAudit(formula_name="Game Uncertainty Index", terms=terms, total=raw_total)
    clipped = max(0, min(100, int(round(raw_total))))
    return clipped, audit
