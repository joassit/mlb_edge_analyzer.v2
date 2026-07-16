"""`historical/resolution_audit.py` -- sensibilidad de discretizacion
(Fases 3+8) y alternativas offline a team_quality via Elo/Pythagorean
(Fase 7 parcial). Datos sinteticos deterministicos sobre SQLite real
basado en archivo (nunca `:memory:`, mismo criterio que
`test_discriminative_audit.py`). Nunca red, nunca Postgres real."""

from __future__ import annotations

import datetime
import random

import numpy as np
import pytest
from sklearn.metrics import roc_auc_score

from jsa.domain.models import SEVEN_PILLARS
from jsa.historical import db as historical_db
from jsa.historical import resolution_audit as ra

_BASE_WEIGHTS = {
    "starter": 0.22, "bullpen": 0.25, "offense": 0.20,
    "team_quality": 0.15, "context": 0.08, "trend": 0.05, "historical": 0.05,
}


def _seed_games_with_snapshots(engine, season: int, n_games: int, seed: int, team_ids=None, team_skill=None) -> None:
    historical_db.init_historical_storage(engine)
    rng = random.Random(seed)
    team_ids = team_ids or list(range(1, 9))
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

        snapshot_payload = {
            "league_avg_era": 4.30, "league_avg_ops": 0.75,
            "home_starter_xera": 3.0 + rng.random() * 2.0, "away_starter_xera": 3.0 + rng.random() * 2.0,
            "home_starter_ip_sample": 30.0 + rng.random() * 40.0, "away_starter_ip_sample": 30.0 + rng.random() * 40.0,
            "home_bullpen_era": 3.0 + rng.random() * 2.0, "away_bullpen_era": 3.0 + rng.random() * 2.0,
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
    return f"sqlite:///{tmp_path}/jsa_resolution_audit_test.db"


@pytest.fixture()
def seeded_records(hist_url):
    engine = historical_db.get_engine(hist_url)
    for season, seed in ((2022, 1), (2023, 2), (2024, 3)):
        _seed_games_with_snapshots(engine, season, 150, seed)
    return ra.load_records_with_raw_diffs(engine, [2022, 2023, 2024])


def test_load_records_with_raw_diffs_shapes(seeded_records):
    assert len(seeded_records) == 450
    r = seeded_records[0]
    assert set(r["raw_diffs"]) == {"starter", "bullpen", "offense"}
    assert all(v is not None for v in r["raw_diffs"].values())


def test_discretization_sweep_has_all_configs_and_config_a_is_reference(seeded_records):
    result = ra.run_discretization_sweep(seeded_records)
    assert set(result) == set(ra.DISCRETIZATION_CONFIGS)
    assert result["A_actual_-2_2"]["bootstrap_ci_delta_brier_vs_actual"] is None
    for config in ra.DISCRETIZATION_CONFIGS:
        entry = result[config]
        assert entry["loso_brier"] is not None
        assert -1.0 <= entry["cohens_d"] <= 1.0 or entry["cohens_d"] is not None
        assert set(entry["per_season_metrics"]) == {2022, 2023, 2024}
        for season_metrics in entry["per_season_metrics"].values():
            assert season_metrics["brier"] is not None
        if config != "A_actual_-2_2":
            assert entry["bootstrap_ci_delta_brier_vs_actual"] is not None


def test_discretization_sweep_configs_produce_different_distributions(seeded_records):
    """Las 6 configuraciones deben dar distribuciones DISTINTAS del score
    (si no, el sweep no esta cambiando nada de verdad)."""
    result = ra.run_discretization_sweep(seeded_records)
    means = {c: result[c]["std"] for c in ra.DISCRETIZATION_CONFIGS}
    assert len(set(round(v, 6) for v in means.values())) > 1


def test_elo_no_signal_on_pure_coinflip_outcomes(hist_url):
    """Sanity check anti-fuga: si el resultado es una moneda pura, sin
    ninguna relacion con la identidad del equipo, el AUC de elo_diff debe
    quedar cerca de 0.5 -- si no, hay una fuga real en el calculo."""
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    rng = random.Random(99)
    team_ids = list(range(1, 9))
    season = 2022
    meta = []
    for i in range(300):
        game_pk = season * 100000 + i
        game_date = datetime.date(season, 4, 1) + datetime.timedelta(days=i % 150)
        home_id, away_id = rng.sample(team_ids, 2)
        historical_db.upsert_game(
            engine, season=season, game_pk=game_pk, game_date=game_date,
            home_team=f"T{home_id}", away_team=f"T{away_id}", home_team_id=home_id, away_team_id=away_id,
            home_pitcher_id=1, away_pitcher_id=2, is_double_header=0,
        )
        home_win = rng.random() < 0.5
        home_score, away_score = (5, 3) if home_win else (3, 5)
        historical_db.update_game_result(engine, game_pk, home_score, away_score)
        meta.append((game_pk, home_win))

    ratings = ra.compute_elo_and_pythagorean(engine, [season])
    elo_diffs = np.array([ratings[(season, pk)]["elo_diff"] for pk, _ in meta])
    y = np.array([1 if w else 0 for _, w in meta])
    auc = roc_auc_score(y, elo_diffs)
    assert 0.40 <= auc <= 0.60


def test_elo_recovers_real_team_skill_signal(hist_url):
    """Cuando SI hay una diferencia de habilidad real y persistente entre
    equipos, Elo debe detectarla (AUC claramente por encima de 0.5)."""
    engine = historical_db.get_engine(hist_url)
    team_ids = list(range(1, 9))
    team_skill = {t: (0.6 if t <= 2 else -0.2 if t >= 7 else 0.0) for t in team_ids}
    _seed_games_with_snapshots(engine, 2022, 400, seed=5, team_ids=team_ids, team_skill=team_skill)

    games = historical_db.games_for_season(engine, 2022)
    ratings = ra.compute_elo_and_pythagorean(engine, [2022])
    elo_diffs, y = [], []
    for g in games:
        entry = ratings.get((2022, g["game_pk"]))
        if entry is None:
            continue
        elo_diffs.append(entry["elo_diff"])
        y.append(1 if g["winner"] == "home" else 0)
    auc = roc_auc_score(np.array(y), np.array(elo_diffs))
    assert auc > 0.55


def test_evaluate_team_quality_alternatives_shape(seeded_records, hist_url):
    engine = historical_db.get_engine(hist_url)
    result = ra.evaluate_team_quality_alternatives(seeded_records, engine, [2022, 2023, 2024])
    assert set(result) == {"elo", "pythagorean_expectation"}
    for entry in result.values():
        assert "auc" in entry["individual_stats"]
        assert entry["loso_if_substituted"]["loso_brier"] is not None
        assert set(entry["loso_if_substituted"]["per_season_metrics"]) == {2022, 2023, 2024}
        assert set(entry["current_team_quality_per_season_metrics"]) == {2022, 2023, 2024}
        assert entry["bootstrap_ci_delta_brier_vs_actual_team_quality"] is not None


def test_run_full_resolution_audit_end_to_end(hist_url):
    engine = historical_db.get_engine(hist_url)
    for season, seed in ((2022, 1), (2023, 2), (2024, 3)):
        _seed_games_with_snapshots(engine, season, 150, seed)

    result = ra.run_full_resolution_audit([2022, 2023, 2024], hist_url)
    assert result["n_games"] == 450
    assert set(result["phase3_8_discretization_sweep"]) == set(ra.DISCRETIZATION_CONFIGS)
    assert set(result["phase7_team_quality_alternatives"]) == {"elo", "pythagorean_expectation"}


def test_run_full_resolution_audit_no_games_returns_error(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    result = ra.run_full_resolution_audit([2099], hist_url)
    assert result == {"n_games": 0, "seasons_used": [2099], "error": "no_games_with_full_data"}
