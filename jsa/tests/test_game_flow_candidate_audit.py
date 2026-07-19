"""`historical/game_flow_candidate_audit.py` -- comparacion LOSO de las 2
hipotesis del Game Flow Engine v1.0 Etapa 1 (GF1 durabilidad del abridor,
GF2 dependencia de bullpen). Datos sinteticos deterministicos sobre
SQLite real basado en archivo (nunca `:memory:`, mismo criterio que el
resto de `historical/`). Nunca red, nunca Postgres real."""

from __future__ import annotations

import datetime
import random

import numpy as np
import pytest
from sklearn.metrics import roc_auc_score

from jsa.domain.models import SEVEN_PILLARS
from jsa.historical import db as historical_db
from jsa.historical import game_flow_candidate_audit as gfa

_BASE_WEIGHTS = {
    "starter": 0.22, "bullpen": 0.25, "offense": 0.20,
    "team_quality": 0.15, "context": 0.08, "trend": 0.05, "historical": 0.05,
}


def _seed_season(engine, season: int, n_games: int, seed: int, *, team_skill: dict | None = None) -> None:
    historical_db.init_historical_storage(engine)
    rng = random.Random(seed)
    team_ids = list(range(1, 9))
    team_skill = team_skill or {t: 0.0 for t in team_ids}

    for i in range(n_games):
        game_pk = season * 100000 + i
        game_date = datetime.date(season, 4, 1) + datetime.timedelta(days=i % 150)
        home_id, away_id = rng.sample(team_ids, 2)
        historical_db.upsert_game(
            engine, season=season, game_pk=game_pk, game_date=game_date,
            home_team=f"T{home_id}", away_team=f"T{away_id}", home_team_id=home_id, away_team_id=away_id,
            home_pitcher_id=1, away_pitcher_id=2, is_double_header=0,
        )
        advantages = {p: rng.choice([-2, -1, 0, 1, 2]) for p in SEVEN_PILLARS}
        evidence_score_raw = sum(_BASE_WEIGHTS[p] * advantages[p] for p in SEVEN_PILLARS)
        skill_edge = team_skill[home_id] - team_skill[away_id]
        home_win = 1 if (evidence_score_raw + skill_edge + rng.gauss(0, 0.25)) > 0 else 0
        home_score, away_score = (5, 3) if home_win else (3, 5)
        historical_db.update_game_result(engine, game_pk, home_score, away_score)

        # `team_skill` mueve tanto la IP proyectada del abridor (mas skill
        # -> abridor mas durable) como el ERA de bullpen (mas skill -> ERA
        # mas bajo) -- para poder inyectar una senal real detectable por
        # GF1/GF2 en el test de recuperacion de senal.
        home_proj_ip = max(3.0, 5.7 + team_skill[home_id] * 1.5 + rng.gauss(0, 0.6))
        away_proj_ip = max(3.0, 5.7 + team_skill[away_id] * 1.5 + rng.gauss(0, 0.6))
        home_bp_era = max(2.0, 4.30 - team_skill[home_id] * 0.8 + rng.gauss(0, 0.4))
        away_bp_era = max(2.0, 4.30 - team_skill[away_id] * 0.8 + rng.gauss(0, 0.4))

        snapshot_payload = {
            "league_avg_era": 4.30, "league_avg_ops": 0.75,
            "home_starter_xera": 3.0 + rng.random() * 2.0, "away_starter_xera": 3.0 + rng.random() * 2.0,
            "home_starter_ip_sample": 30.0 + rng.random() * 40.0, "away_starter_ip_sample": 30.0 + rng.random() * 40.0,
            "home_starter_projected_ip": home_proj_ip, "away_starter_projected_ip": away_proj_ip,
            "home_bullpen_era": home_bp_era, "away_bullpen_era": away_bp_era,
            "home_bullpen_ip_sample": 50.0 + rng.random() * 80.0, "away_bullpen_ip_sample": 50.0 + rng.random() * 80.0,
            "home_closer_available": True, "away_closer_available": True,
            "home_ops": 0.65 + rng.random() * 0.2, "away_ops": 0.65 + rng.random() * 0.2,
            "home_ops_pa_sample": 150, "away_ops_pa_sample": 150,
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
    return f"sqlite:///{tmp_path}/jsa_game_flow_candidate_audit_test.db"


@pytest.fixture()
def seeded_records(hist_url):
    engine = historical_db.get_engine(hist_url)
    for season, seed in ((2022, 1), (2023, 2), (2024, 3)):
        _seed_season(engine, season, 150, seed)
    return gfa.load_records_with_game_flow_candidates(engine, [2022, 2023, 2024])


def test_load_records_with_game_flow_candidates_shapes(seeded_records):
    assert len(seeded_records) == 450
    r = seeded_records[0]
    assert set(r["game_flow_diffs"]) == set(gfa.ALL_HYPOTHESES)
    assert all(v is not None for v in r["game_flow_diffs"].values())


def test_prob_ip_ge_is_monotonic_in_projected_ip():
    low = gfa._prob_ip_ge(4.5)
    mid = gfa._prob_ip_ge(6.0)
    high = gfa._prob_ip_ge(7.5)
    assert low < mid < high
    assert gfa._prob_ip_ge(None) is None


def test_expected_bullpen_ip_decreases_as_starter_goes_deeper():
    short = gfa._expected_bullpen_ip(4.0)
    long_ = gfa._expected_bullpen_ip(8.0)
    assert short > long_
    assert gfa._expected_bullpen_ip(None) == gfa._GAME_INNINGS / 2.0


def test_game_flow_candidates_no_signal_on_pure_coinflip(hist_url):
    """Sanity check anti-fuga: sin relacion real entre team_skill y el
    resultado, el AUC de cada hipotesis debe quedar cerca de 0.5."""
    engine = historical_db.get_engine(hist_url)
    _seed_season(engine, 2022, 300, seed=99)  # team_skill=None -> todos en 0.0, home_win es ruido puro
    records = gfa.load_records_with_game_flow_candidates(engine, [2022])
    y = np.array([r["home_win"] for r in records])
    for hyp in gfa.ALL_HYPOTHESES:
        diffs = np.array([r["game_flow_diffs"][hyp] for r in records])
        auc = roc_auc_score(y, diffs)
        assert 0.35 <= auc <= 0.65, f"{hyp}: auc={auc} fuera del rango de un coinflip puro"


def test_game_flow_candidates_recover_injected_signal(hist_url):
    """Cuando SI hay una diferencia de habilidad real y persistente
    (reflejada tanto en el resultado como en projected_ip/bullpen_era),
    ambas hipotesis deben detectarla."""
    engine = historical_db.get_engine(hist_url)
    team_ids = list(range(1, 9))
    team_skill = {t: (0.6 if t <= 2 else -0.6 if t >= 7 else 0.0) for t in team_ids}
    for season, seed in ((2022, 11), (2023, 12), (2024, 13)):
        _seed_season(engine, season, 400, seed=seed, team_skill=team_skill)

    records = gfa.load_records_with_game_flow_candidates(engine, [2022, 2023, 2024])
    y = np.array([r["home_win"] for r in records])
    for hyp in gfa.ALL_HYPOTHESES:
        diffs = np.array([r["game_flow_diffs"][hyp] for r in records])
        auc = roc_auc_score(y, diffs)
        assert auc > 0.55, f"{hyp} no detecto la senal inyectada: auc={auc}"


def test_evaluate_game_flow_candidates_shape(seeded_records):
    result = gfa.evaluate_game_flow_candidates(seeded_records)
    assert set(result) == set(gfa.ALL_HYPOTHESES)
    for hyp, entry in result.items():
        assert entry["target_pillar"] == gfa._TARGET_PILLAR[hyp]
        assert "auc" in entry["individual_stats"]
        assert entry["loso_if_substituted"]["loso_brier"] is not None
        assert set(entry["loso_if_substituted"]["per_season_metrics"]) == {2022, 2023, 2024}
        assert entry["bootstrap_ci_delta_brier_vs_current"] is not None


def test_run_full_game_flow_candidate_audit_end_to_end(hist_url):
    engine = historical_db.get_engine(hist_url)
    for season, seed in ((2022, 1), (2023, 2), (2024, 3)):
        _seed_season(engine, season, 150, seed)

    result = gfa.run_full_game_flow_candidate_audit([2022, 2023, 2024], hist_url)
    assert result["n_games"] == 450
    assert set(result["phase_loso_comparison"]) == set(gfa.ALL_HYPOTHESES)


def test_run_full_game_flow_candidate_audit_no_games_returns_error(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    result = gfa.run_full_game_flow_candidate_audit([2099], hist_url)
    assert result == {"n_games": 0, "seasons_used": [2099], "error": "no_games_with_full_data"}
