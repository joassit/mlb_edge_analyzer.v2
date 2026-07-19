"""`cross_model/sync_jsa.py` -- sincroniza evidence_score_raw (JSA) y
GF1/GF2 (Game Flow) desde una base historica de JSA sembrada
sinteticamente hacia `unified_model_predictions`. Nunca escribe a
`jsa/historical/db.py` -- solo lee. SQLite real basado en archivo."""

from __future__ import annotations

import datetime
import random

import pytest

from jsa.domain.models import SEVEN_PILLARS
from jsa.historical import db as historical_db

from cross_model import db as unified_db
from cross_model import sync_jsa

_BASE_WEIGHTS = {
    "starter": 0.22, "bullpen": 0.25, "offense": 0.20,
    "team_quality": 0.15, "context": 0.08, "trend": 0.05, "historical": 0.05,
}


def _seed_season(jsa_engine, season: int, n_games: int, seed: int) -> None:
    historical_db.init_historical_storage(jsa_engine)
    rng = random.Random(seed)
    team_ids = list(range(1, 9))

    for i in range(n_games):
        game_pk = season * 100000 + i
        game_date = datetime.date(season, 4, 1) + datetime.timedelta(days=i % 150)
        home_id, away_id = rng.sample(team_ids, 2)
        historical_db.upsert_game(
            jsa_engine, season=season, game_pk=game_pk, game_date=game_date,
            home_team=f"T{home_id}", away_team=f"T{away_id}", home_team_id=home_id, away_team_id=away_id,
            home_pitcher_id=1, away_pitcher_id=2, is_double_header=0,
        )
        advantages = {p: rng.choice([-2, -1, 0, 1, 2]) for p in SEVEN_PILLARS}
        evidence_score_raw = sum(_BASE_WEIGHTS[p] * advantages[p] for p in SEVEN_PILLARS)
        home_win = 1 if (evidence_score_raw + rng.gauss(0, 0.25)) > 0 else 0
        home_score, away_score = (5, 3) if home_win else (3, 5)
        historical_db.update_game_result(jsa_engine, game_pk, home_score, away_score)

        historical_db.persist_historical_snapshot(
            jsa_engine, season=season, game_pk=game_pk, game_date=game_date,
            snapshot_hash=f"hash-{game_pk}",
            snapshot_payload={
                "league_avg_era": 4.30,
                "home_starter_projected_ip": 5.0 + rng.random() * 2.0,
                "away_starter_projected_ip": 5.0 + rng.random() * 2.0,
                "home_bullpen_era": 3.5 + rng.random() * 1.5,
                "away_bullpen_era": 3.5 + rng.random() * 1.5,
                "home_bullpen_ip_sample": 60.0, "away_bullpen_ip_sample": 60.0,
                "home_closer_available": True, "away_closer_available": True,
            },
        )
        pillar_advantages = [
            {"pillar": p, "advantage": advantages[p], "explanation": "sintetico", "pillar_contract_version": f"{p}@1.0.0"}
            for p in SEVEN_PILLARS
        ]
        historical_db.persist_historical_report(
            jsa_engine, run_id=f"run-{season}", season=season, game_pk=game_pk, game_date=game_date,
            report_payload={
                "manifest_status": "valid", "evidence_score_raw": evidence_score_raw,
                "pillar_advantages": pillar_advantages, "final_weights": dict(_BASE_WEIGHTS),
            },
        )


@pytest.fixture()
def jsa_hist_url(tmp_path):
    return f"sqlite:///{tmp_path}/jsa_historical_for_sync_test.db"


@pytest.fixture()
def unified_url(tmp_path):
    return f"sqlite:///{tmp_path}/unified_for_sync_test.db"


def test_sync_jsa_evidence_score_writes_one_row_per_game(jsa_hist_url, unified_url):
    engine = historical_db.get_engine(jsa_hist_url)
    _seed_season(engine, 2023, 100, seed=1)

    n = sync_jsa.sync_jsa_evidence_score(jsa_hist_url, unified_url, [2023])
    assert n == 100

    unified_engine = unified_db.get_engine(unified_url)
    result = unified_db.accuracy_by_system_and_model(unified_engine)
    assert len(result) == 1
    assert result[0]["system"] == "jsa"
    assert result[0]["model_name"] == "evidence_score_raw"
    assert result[0]["n_games"] == 100
    assert 0.0 <= result[0]["accuracy"] <= 1.0


def test_sync_game_flow_candidates_writes_rows_per_hypothesis_with_coverage(jsa_hist_url, unified_url):
    engine = historical_db.get_engine(jsa_hist_url)
    _seed_season(engine, 2023, 100, seed=2)

    n = sync_jsa.sync_game_flow_candidates(jsa_hist_url, unified_url, [2023])
    # Ambas hipotesis tienen datos en el 100% de estos juegos sinteticos
    # (projected_ip y bullpen_era siempre presentes) -> 200 filas.
    assert n == 200

    unified_engine = unified_db.get_engine(unified_url)
    result = unified_db.accuracy_by_system_and_model(unified_engine)
    models = {(r["system"], r["model_name"]) for r in result}
    assert ("game_flow", "gf1_starter_durability") in models
    assert ("game_flow", "gf2_bullpen_dependency") in models


def test_sync_is_idempotent_across_both_systems(jsa_hist_url, unified_url):
    engine = historical_db.get_engine(jsa_hist_url)
    _seed_season(engine, 2023, 50, seed=3)

    sync_jsa.sync_jsa_evidence_score(jsa_hist_url, unified_url, [2023])
    sync_jsa.sync_game_flow_candidates(jsa_hist_url, unified_url, [2023])
    # Correr todo de nuevo no debe duplicar filas.
    sync_jsa.sync_jsa_evidence_score(jsa_hist_url, unified_url, [2023])
    sync_jsa.sync_game_flow_candidates(jsa_hist_url, unified_url, [2023])

    unified_engine = unified_db.get_engine(unified_url)
    result = unified_db.accuracy_by_system_and_model(unified_engine)
    totals = {(r["system"], r["model_name"]): r["n_games"] for r in result}
    assert totals[("jsa", "evidence_score_raw")] == 50
    assert totals[("game_flow", "gf1_starter_durability")] == 50
    assert totals[("game_flow", "gf2_bullpen_dependency")] == 50


def test_cross_system_query_compares_jsa_vs_game_flow_for_same_games(jsa_hist_url, unified_url):
    """La demostracion end-to-end del pedido original: comparar,
    con una sola consulta SQL sobre una tabla, la precision de JSA
    (evidence_score_raw) contra Game Flow (GF1/GF2) para los MISMOS
    juegos historicos."""
    engine = historical_db.get_engine(jsa_hist_url)
    _seed_season(engine, 2022, 200, seed=7)

    sync_jsa.sync_jsa_evidence_score(jsa_hist_url, unified_url, [2022])
    sync_jsa.sync_game_flow_candidates(jsa_hist_url, unified_url, [2022])

    unified_engine = unified_db.get_engine(unified_url)
    result = unified_db.accuracy_by_system_and_model(unified_engine, seasons=[2022])
    by_key = {(r["system"], r["model_name"]): r for r in result}
    assert {("jsa", "evidence_score_raw"), ("game_flow", "gf1_starter_durability"), ("game_flow", "gf2_bullpen_dependency")} <= set(by_key)
    for entry in by_key.values():
        assert entry["n_games"] == 200
