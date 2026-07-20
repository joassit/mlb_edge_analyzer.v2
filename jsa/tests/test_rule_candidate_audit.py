"""`historical/rule_candidate_audit.py` -- Fase 3 (Seccion 6.6 + 12.8):
primera evaluacion formal de las reglas heredadas contra el historico
real. Datos sinteticos deterministicos sobre SQLite real basado en
archivo (nunca `:memory:`, mismo criterio que el resto de
`historical/`). Nunca red, nunca Postgres real."""

from __future__ import annotations

import datetime
import random

import pytest

from jsa.domain.models import SEVEN_PILLARS
from jsa.engine.rule_definitions import RULE_SPECS_BY_ID
from jsa.historical import db as historical_db
from jsa.historical import rule_candidate_audit as rca

_BASE_WEIGHTS = {
    "starter": 0.22, "bullpen": 0.25, "offense": 0.20,
    "team_quality": 0.15, "context": 0.08, "trend": 0.05, "historical": 0.05,
}


def _seed_season(engine, season: int, n_games: int, seed: int, *, trigger_rule: str | None = None, inject_true_signal: bool = False) -> None:
    """Mitad de los juegos (i par) disparan `trigger_rule` (si se indica);
    la otra mitad no. Si `inject_true_signal=True`, el resultado real de
    los juegos DISPARADOS se genera con los pesos que el propio
    `weight_adjustments` de la regla propone (en vez de los pesos base) --
    exactamente el gap que la regla debe cerrar para pasar las 3 pruebas
    de significancia. `evidence_score_raw` persistido SIEMPRE usa los
    pesos base (el estado real de produccion hoy, donde ninguna regla se
    aplica jamas)."""
    historical_db.init_historical_storage(engine)
    rng = random.Random(seed)
    team_ids = list(range(1, 9))

    for i in range(n_games):
        game_pk = season * 100000 + i
        game_date = datetime.date(season, 4, 1) + datetime.timedelta(days=i % 150)
        home_id, away_id = rng.sample(team_ids, 2)
        fires = (i % 2 == 0)

        home_proj_ip = away_proj_ip = 5.7
        if trigger_rule == "long_outing" and fires:
            home_proj_ip = away_proj_ip = 7.2
        elif trigger_rule == "short_outing_bullpen_game" and fires:
            home_proj_ip = away_proj_ip = 4.0

        home_key_injuries = ["p1", "p2"] if (trigger_rule == "key_offensive_injuries" and fires) else []
        is_double_header = bool(trigger_rule == "double_header" and fires)
        travel_distance = 2500.0 if (trigger_rule == "extreme_travel" and fires) else 100.0

        historical_db.upsert_game(
            engine, season=season, game_pk=game_pk, game_date=game_date,
            home_team=f"T{home_id}", away_team=f"T{away_id}", home_team_id=home_id, away_team_id=away_id,
            home_pitcher_id=1, away_pitcher_id=2, is_double_header=int(is_double_header),
        )

        advantages = {p: rng.choice([-2, -1, 0, 1, 2]) for p in SEVEN_PILLARS}
        evidence_score_raw = sum(_BASE_WEIGHTS[p] * advantages[p] for p in SEVEN_PILLARS)

        if inject_true_signal and fires and trigger_rule is not None:
            true_weights = dict(_BASE_WEIGHTS)
            for pillar, delta in RULE_SPECS_BY_ID[trigger_rule].weight_adjustments.items():
                true_weights[pillar] += delta
            true_score = sum(true_weights[p] * advantages[p] for p in SEVEN_PILLARS)
        else:
            true_score = evidence_score_raw

        home_win = 1 if (true_score + rng.gauss(0, 0.15)) > 0 else 0
        home_score, away_score = (5, 3) if home_win else (3, 5)
        historical_db.update_game_result(engine, game_pk, home_score, away_score)

        snapshot_payload = {
            "game_id": f"game-{game_pk}", "game_pk": game_pk, "game_date": game_date.isoformat(),
            "season": season, "home_team": f"T{home_id}", "away_team": f"T{away_id}",
            "home_starter_projected_ip": home_proj_ip, "away_starter_projected_ip": away_proj_ip,
            "home_key_injuries": home_key_injuries, "away_key_injuries": [],
            "is_double_header": is_double_header, "travel_distance": travel_distance,
            "league_avg_era": 4.30,
        }
        historical_db.persist_historical_snapshot(
            engine, season=season, game_pk=game_pk, game_date=game_date,
            snapshot_hash=f"hash-{game_pk}", snapshot_payload=snapshot_payload,
        )
        pillar_advantages = [
            {"pillar": p, "advantage": advantages[p], "explanation": "sintetico", "pillar_contract_version": f"{p}@1.0.0"}
            for p in SEVEN_PILLARS
        ]
        historical_db.persist_historical_report(
            engine, run_id=f"run-{season}", season=season, game_pk=game_pk, game_date=game_date,
            report_payload={
                "manifest_status": "valid", "evidence_score_raw": evidence_score_raw,
                "pillar_advantages": pillar_advantages, "final_weights": dict(_BASE_WEIGHTS),
            },
        )


@pytest.fixture()
def hist_url(tmp_path):
    return f"sqlite:///{tmp_path}/jsa_rule_candidate_audit_test.db"


def test_testable_rule_ids_excludes_bullpen_fatigue():
    assert "bullpen_fatigue" not in rca.TESTABLE_RULE_IDS
    assert "bullpen_fatigue" in rca.UNTESTABLE_RULE_IDS
    assert set(rca.TESTABLE_RULE_IDS) | set(rca.UNTESTABLE_RULE_IDS) == set(RULE_SPECS_BY_ID)


def test_trigger_fired_matches_context_detector_thresholds():
    spec = RULE_SPECS_BY_ID["long_outing"]
    base_payload = {
        "game_id": "g1", "game_pk": 1, "game_date": "2024-04-01", "season": 2024,
        "home_team": "T1", "away_team": "T2",
    }
    fires = rca._trigger_fired(spec, {**base_payload, "home_starter_projected_ip": 7.0, "away_starter_projected_ip": 7.0})
    no_fire = rca._trigger_fired(spec, {**base_payload, "home_starter_projected_ip": 5.5, "away_starter_projected_ip": 5.5})
    assert fires is True
    assert no_fire is False


def test_alt_score_unchanged_when_rule_not_fired():
    spec = RULE_SPECS_BY_ID["long_outing"]
    record = {
        "evidence_score_raw": 0.35, "weights": dict(_BASE_WEIGHTS),
        "advantages": {p: 1 for p in SEVEN_PILLARS},
    }
    assert rca._alt_score_for_rule(spec, record, fired=False) == 0.35


def test_alt_score_redistributes_weight_when_fired():
    spec = RULE_SPECS_BY_ID["long_outing"]  # starter +0.06, bullpen -0.06
    advantages = {p: 0 for p in SEVEN_PILLARS}
    advantages["starter"] = 2
    advantages["bullpen"] = -2
    record = {"evidence_score_raw": 0.0, "weights": dict(_BASE_WEIGHTS), "advantages": advantages}
    alt_score = rca._alt_score_for_rule(spec, record, fired=True)
    # starter sube de peso (advantage positivo) y bullpen baja (advantage negativo)
    # -> el score alternativo debe subir respecto al baseline con pesos base.
    assert alt_score > record["evidence_score_raw"]


def test_load_records_with_rule_triggers_shapes(hist_url):
    engine = historical_db.get_engine(hist_url)
    _seed_season(engine, 2022, 100, seed=1, trigger_rule="long_outing")
    records = rca.load_records_with_rule_triggers(engine, [2022])
    assert len(records) == 100
    r = records[0]
    assert set(r["rule_triggers"]) == set(rca.TESTABLE_RULE_IDS)
    # La mitad de los juegos (i par) dispara long_outing en este seed.
    n_fired = sum(1 for rec in records if rec["rule_triggers"]["long_outing"])
    assert n_fired == 50


def test_evaluate_rule_candidates_shape(hist_url):
    engine = historical_db.get_engine(hist_url)
    _seed_season(engine, 2022, 150, seed=2)
    _seed_season(engine, 2023, 150, seed=3)
    records = rca.load_records_with_rule_triggers(engine, [2022, 2023])
    result = rca.evaluate_rule_candidates(records)
    assert set(result) == set(rca.TESTABLE_RULE_IDS)
    for rule_id, entry in result.items():
        assert entry["n_games"] == 300
        assert 0 <= entry["n_triggered"] <= 300
        assert entry["loso_if_rule_active"]["loso_brier"] is not None
        assert entry["current_loso"]["loso_brier"] is not None
        sig_report = entry["significance"]
        assert "passes_all_three" in sig_report


def test_rule_candidates_no_signal_on_pure_coinflip(hist_url):
    """Sanity check anti-fuga: sin ventaja real inyectada cuando la regla
    dispara, ninguna regla deberia pasar las 3 pruebas de significancia."""
    engine = historical_db.get_engine(hist_url)
    for season, seed in ((2022, 10), (2023, 11), (2024, 12)):
        _seed_season(engine, season, 200, seed=seed, trigger_rule="long_outing", inject_true_signal=False)
    records = rca.load_records_with_rule_triggers(engine, [2022, 2023, 2024])
    result = rca.evaluate_rule_candidates(records)
    for rule_id, entry in result.items():
        assert entry["significance"]["passes_all_three"] is False, f"{rule_id} paso sin senal real inyectada"


def test_rule_candidates_recover_injected_signal(hist_url):
    """Cuando la regla dispara Y ese subconjunto de juegos SI tiene una
    ventaja real para home no capturada por evidence_score_raw, sustituir
    los pesos en esos juegos debe mejorar el Brier lo suficiente para
    pasar las 3 pruebas de significancia."""
    engine = historical_db.get_engine(hist_url)
    for season, seed in ((2022, 20), (2023, 21), (2024, 22), (2025, 23), (2026, 24)):
        _seed_season(engine, season, 600, seed=seed, trigger_rule="long_outing", inject_true_signal=True)
    records = rca.load_records_with_rule_triggers(engine, [2022, 2023, 2024, 2025, 2026])
    result = rca.evaluate_rule_candidates(records)
    assert result["long_outing"]["significance"]["passes_all_three"] is True


def test_run_full_rule_candidate_audit_end_to_end(hist_url):
    engine = historical_db.get_engine(hist_url)
    for season, seed in ((2022, 1), (2023, 2), (2024, 3)):
        _seed_season(engine, season, 150, seed=seed)
    result = rca.run_full_rule_candidate_audit([2022, 2023, 2024], hist_url)
    assert result["n_games"] == 450
    assert set(result["rule_results"]) == set(rca.TESTABLE_RULE_IDS)
    assert result["untestable_rules"] == list(rca.UNTESTABLE_RULE_IDS)


def test_run_full_rule_candidate_audit_no_games_returns_error(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    result = rca.run_full_rule_candidate_audit([2099], hist_url)
    assert result == {"n_games": 0, "seasons_used": [2099], "error": "no_games_with_full_data"}
