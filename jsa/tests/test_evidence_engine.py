import datetime

from jsa import config
from jsa.config import BASE_PILLAR_WEIGHTS
from jsa.domain.models import PillarAdvantage, PillarWeights, build_game_snapshot
from jsa.engine.evidence_engine import (
    apply_consistency_penalty,
    compute_cri,
    compute_evidence_score,
    compute_feature_contribution,
    compute_uncertainty_index,
)
from jsa.engine.context_detector import detect_context


def _snap(**overrides):
    fields = dict(game_id="g1", game_date=datetime.date(2026, 7, 13), season=2026, home_team="NYY", away_team="BOS")
    fields.update(overrides)
    return build_game_snapshot(**fields)


def _pillars(advantages: dict[str, int]) -> list[PillarAdvantage]:
    from jsa.engine.pillars.base import PILLAR_CONTRACT_VERSIONS

    return [
        PillarAdvantage(pillar=p, advantage=adv, explanation="test", pillar_contract_version=PILLAR_CONTRACT_VERSIONS[p])
        for p, adv in advantages.items()
    ]


def test_evidence_score_matches_manual_sum():
    weights = PillarWeights(**BASE_PILLAR_WEIGHTS)
    pillars = _pillars({p: 1 for p in BASE_PILLAR_WEIGHTS})
    score, audit = compute_evidence_score(pillars, weights)
    expected = sum(BASE_PILLAR_WEIGHTS[p] * 1 for p in BASE_PILLAR_WEIGHTS)
    assert abs(score - expected) < 1e-9
    assert audit.verify(tolerance=1e-9)


def test_math_audit_reproducible_within_tolerance():
    weights = PillarWeights(**BASE_PILLAR_WEIGHTS)
    pillars = _pillars({"starter": 2, "bullpen": -1, "offense": 0, "team_quality": 1, "context": -2, "trend": 1, "historical": 0})
    _, audit = compute_evidence_score(pillars, weights)
    recomputed = sum(t.product for t in audit.terms)
    assert abs(recomputed - audit.total) <= 1e-9


def test_dominance_detector_flags_pillar_over_40_percent():
    weights = PillarWeights(**BASE_PILLAR_WEIGHTS)
    # bullpen (peso 0.25) con advantage +2 vs. el resto neutral concentra
    # toda la contribucion en un solo pilar -> >40%.
    pillars = _pillars({"starter": 0, "bullpen": 2, "offense": 0, "team_quality": 0, "context": 0, "trend": 0, "historical": 0})
    contributions = compute_feature_contribution(pillars, weights)
    bullpen_entry = next(c for c in contributions if c.pillar == "bullpen")
    assert bullpen_entry.dominance_warning is True
    assert bullpen_entry.percentage_contribution == 1.0


def test_no_dominance_when_all_neutral():
    weights = PillarWeights(**BASE_PILLAR_WEIGHTS)
    pillars = _pillars({p: 0 for p in BASE_PILLAR_WEIGHTS})
    contributions = compute_feature_contribution(pillars, weights)
    assert all(c.percentage_contribution == 0.0 for c in contributions)
    assert not any(c.dominance_warning for c in contributions)


def test_cri_formula_matches_spec_components():
    snap = _snap(starters_confirmed=True, lineups_official=True, bullpen_usage_known=True, no_last_minute_changes=True)
    cri, audit, _ = compute_cri(snap)
    # 18+18+12+12 = 60, sin xera/xfip, sin missing_projected_ip (ambos None -> True -> -10)
    assert cri == 50
    assert audit.verify()


def test_cri_clips_to_zero_and_hundred():
    snap = _snap()  # todo False/None -> raw = -10 (missing_projected_ip)
    cri, _, _ = compute_cri(snap)
    assert cri == 0


def test_cri_max_possible_score_is_75_not_100():
    """compute_cri() clippea a [0,100], pero la suma de componentes
    positivos de CRI_COMPONENTS nunca pasa de 75 -- ningun juego, ni
    real ni sintetico, puede superar ese techo. Cualquier umbral que
    dependa de cri_score (GATE_CRI_MIN, CRI_THRESHOLD_CLEAR_FAVORITE)
    tiene que quedar por debajo de este numero o queda estructuralmente
    inalcanzable (bug real encontrado 2026-07-20: GATE_CRI_MIN=85 hacia
    que el Confidence Gate nunca pudiera pasar por CRI, en produccion
    en vivo tambien)."""
    snap = _snap(
        starters_confirmed=True, lineups_official=True, bullpen_usage_known=True, no_last_minute_changes=True,
        home_starter_xera=3.5, away_starter_xera=4.0, home_starter_xfip=3.6, away_starter_xfip=3.9,
        home_starter_projected_ip=6.0, away_starter_projected_ip=5.5,
    )
    cri, _, _ = compute_cri(snap)
    assert cri == 75
    assert config.GATE_CRI_MIN <= 75
    assert config.CRI_THRESHOLD_CLEAR_FAVORITE <= 75


def test_uncertainty_index_base_and_additions():
    snap = _snap(is_double_header=True, weather_temp_f=30, home_key_injuries=["a", "b"])
    ctx = detect_context(snap)
    unc, audit = compute_uncertainty_index(snap, ctx)
    assert unc == 40 + 8 + 12 + 8  # base + double_header + extreme_weather + 2*4 injuries
    assert audit.verify()


def test_uncertainty_index_capped_at_100():
    snap = _snap(
        is_double_header=True, weather_temp_f=20, travel_distance=3000,
        home_bullpen_ip_last_3_days=15, away_bullpen_ip_last_3_days=15,
        home_key_injuries=["a", "b", "c", "d", "e", "f"], away_key_injuries=[],
    )
    ctx = detect_context(snap)
    unc, _ = compute_uncertainty_index(snap, ctx)
    assert unc <= 100


def test_consistency_penalty_applies_only_when_conflicting():
    assert apply_consistency_penalty(90, "conflicting") == 80
    assert apply_consistency_penalty(90, "aligned") == 90
    assert apply_consistency_penalty(5, "conflicting") == 0  # nunca negativo
