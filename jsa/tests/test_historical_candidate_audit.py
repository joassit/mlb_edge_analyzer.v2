"""`historical/historical_candidate_audit.py` -- auditoria descriptiva +
comparacion LOSO de los 4 candidatos de historial head-to-head para el
pilar Historical Favorite Context. 100% offline desde `historical_game`
ya ingerido. Datos sinteticos deterministicos sobre SQLite real basado en
archivo (nunca `:memory:`). Nunca red, nunca Postgres real."""

from __future__ import annotations

import datetime
import random

import numpy as np
import pytest
from sklearn.metrics import roc_auc_score

from jsa.domain.models import SEVEN_PILLARS
from jsa.historical import db as historical_db
from jsa.historical import historical_candidate_audit as hca

_BASE_WEIGHTS = {
    "starter": 0.22, "bullpen": 0.25, "offense": 0.20,
    "team_quality": 0.15, "context": 0.08, "trend": 0.05, "historical": 0.05,
}


def _seed_games(engine, season: int, n_games: int, seed: int, *, team_skill: dict | None = None, day_offset: int = 0) -> None:
    """`day_offset` permite encadenar temporadas en fechas reales
    distintas (2022 antes que 2023, etc.) para poder probar que el
    historial head-to-head persiste ENTRE temporadas."""
    historical_db.init_historical_storage(engine)
    rng = random.Random(seed)
    team_ids = list(range(1, 9))
    team_skill = team_skill or {t: 0.0 for t in team_ids}
    for i in range(n_games):
        game_pk = season * 100000 + i
        game_date = datetime.date(2022, 1, 1) + datetime.timedelta(days=day_offset + i % 150)
        home_id, away_id = rng.sample(team_ids, 2)
        historical_db.upsert_game(
            engine, season=season, game_pk=game_pk, game_date=game_date,
            home_team=f"T{home_id}", away_team=f"T{away_id}", home_team_id=home_id, away_team_id=away_id,
            home_pitcher_id=1, away_pitcher_id=2, is_double_header=0,
        )
        advantages = {p: rng.choice([-2, -1, 0, 1, 2]) for p in SEVEN_PILLARS}
        advantages["historical"] = 0  # sigue siendo stub en produccion -- SIEMPRE 0, igual que hoy
        evidence_score_raw = sum(_BASE_WEIGHTS[p] * advantages[p] for p in SEVEN_PILLARS)

        skill_edge = team_skill[home_id] - team_skill[away_id]
        home_win = 1 if (evidence_score_raw + skill_edge + rng.gauss(0, 0.25)) > 0 else 0
        home_score, away_score = (5, 3) if home_win else (3, 5)
        historical_db.update_game_result(engine, game_pk, home_score, away_score)

        historical_db.persist_historical_snapshot(
            engine, season=season, game_pk=game_pk, game_date=game_date,
            snapshot_hash=f"hash-{game_pk}", snapshot_payload={"league_avg_era": 4.30, "league_avg_ops": 0.75},
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
    return f"sqlite:///{tmp_path}/jsa_historical_candidate_audit_test.db"


@pytest.fixture()
def seeded_records(hist_url):
    engine = historical_db.get_engine(hist_url)
    for season, seed, offset in ((2022, 1, 0), (2023, 2, 200), (2024, 3, 400)):
        _seed_games(engine, season, 150, seed, day_offset=offset)
    return hca.load_records_with_h2h_candidates(engine, [2022, 2023, 2024])


def test_load_records_with_h2h_candidates_shapes(seeded_records):
    assert len(seeded_records) == 450
    r = seeded_records[0]
    assert set(r["h2h_candidate_diffs"]) == set(hca.HISTORICAL_CANDIDATES)
    assert "n_meetings" in r
    # con 8 equipos y 450 juegos repartidos en 3 temporadas, deberia haber
    # cobertura real de reencuentros (no todos None).
    assert any(r["n_meetings"] > 0 for r in seeded_records)


def test_head_to_head_history_never_uses_future_games(hist_url):
    """Sanity check de integridad point-in-time: el primer enfrentamiento
    real entre dos equipos especificos debe tener n_meetings=0 (nunca se
    filtra el resultado del juego actual ni de ninguno posterior)."""
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    historical_db.upsert_game(
        engine, season=2022, game_pk=1, game_date=datetime.date(2022, 4, 1),
        home_team="T1", away_team="T2", home_team_id=1, away_team_id=2,
        home_pitcher_id=1, away_pitcher_id=2, is_double_header=0,
    )
    historical_db.update_game_result(engine, 1, 5, 3)
    h2h = hca.compute_head_to_head_history(engine, [2022])
    assert h2h[(2022, 1)]["n_meetings"] == 0
    assert all(h2h[(2022, 1)][c] is None for c in hca.HISTORICAL_CANDIDATES)


def test_head_to_head_history_persists_across_seasons(hist_url):
    """El historial NO se reinicia entre temporadas -- un enfrentamiento de
    2022 debe contar para el mismo par de equipos en 2023."""
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    historical_db.upsert_game(
        engine, season=2022, game_pk=1, game_date=datetime.date(2022, 4, 1),
        home_team="T1", away_team="T2", home_team_id=1, away_team_id=2,
        home_pitcher_id=1, away_pitcher_id=2, is_double_header=0,
    )
    historical_db.update_game_result(engine, 1, 5, 3)
    historical_db.upsert_game(
        engine, season=2023, game_pk=2, game_date=datetime.date(2023, 4, 1),
        home_team="T2", away_team="T1", home_team_id=2, away_team_id=1,
        home_pitcher_id=1, away_pitcher_id=2, is_double_header=0,
    )
    historical_db.update_game_result(engine, 2, 5, 3)

    h2h = hca.compute_head_to_head_history(engine, [2022, 2023])
    assert h2h[(2022, 1)]["n_meetings"] == 0
    assert h2h[(2023, 2)]["n_meetings"] == 1
    # T1 gano el enfrentamiento de 2022 como local; en 2023 T1 es visitante
    # -- el candidato debe reflejar que el equipo HOY local (T2) perdio
    # ese enfrentamiento anterior (diff negativo desde la perspectiva de T2).
    assert h2h[(2023, 2)]["h2h_win_pct_all_time"] < 0


def test_descriptive_audit_shape(seeded_records):
    result = hca.run_descriptive_audit(seeded_records)
    assert 0.0 < result["n_games_with_prior_meeting_coverage_pct"] <= 1.0
    assert set(result["per_candidate"]) == set(hca.HISTORICAL_CANDIDATES)
    for stats in result["per_candidate"].values():
        assert stats["n_games"] == 450
        if stats["n_non_null"] > 0:
            assert stats["min"] <= stats["median"] <= stats["max"]


def test_evaluate_historical_candidates_shape(seeded_records):
    result = hca.evaluate_historical_candidates(seeded_records)
    assert set(result) == set(hca.HISTORICAL_CANDIDATES)
    for entry in result.values():
        assert "auc" in entry["individual_stats"]
        assert entry["loso_if_substituted"]["loso_brier"] is not None
        assert set(entry["loso_if_substituted"]["per_season_metrics"]) == {2022, 2023, 2024}
        assert set(entry["current_historical_per_season_metrics"]) == {2022, 2023, 2024}
        assert entry["bootstrap_ci_delta_brier_vs_actual_historical"] is not None


def test_historical_candidate_no_signal_on_pure_coinflip(hist_url):
    """Sanity check anti-fuga: si el resultado es una moneda pura sin
    ninguna relacion con la identidad de los equipos, el AUC de cada
    candidato debe quedar cerca de 0.5."""
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    rng = random.Random(99)
    team_ids = list(range(1, 9))
    meta = []
    for i in range(400):
        game_pk = 2022_00000 + i
        game_date = datetime.date(2022, 4, 1) + datetime.timedelta(days=i % 150)
        home_id, away_id = rng.sample(team_ids, 2)
        historical_db.upsert_game(
            engine, season=2022, game_pk=game_pk, game_date=game_date,
            home_team=f"T{home_id}", away_team=f"T{away_id}", home_team_id=home_id, away_team_id=away_id,
            home_pitcher_id=1, away_pitcher_id=2, is_double_header=0,
        )
        home_win = rng.random() < 0.5
        home_score, away_score = (5, 3) if home_win else (3, 5)
        historical_db.update_game_result(engine, game_pk, home_score, away_score)
        meta.append((game_pk, home_win))

    h2h = hca.compute_head_to_head_history(engine, [2022])
    for candidate in hca.HISTORICAL_CANDIDATES:
        values, ys = [], []
        for game_pk, won in meta:
            v = h2h[(2022, game_pk)][candidate]
            if v is not None:
                values.append(v)
                ys.append(1 if won else 0)
        if len(values) < 10:
            continue  # muestra insuficiente para un AUC confiable en este candidato
        auc = roc_auc_score(np.array(ys), np.array(values))
        assert 0.35 <= auc <= 0.65, f"{candidate}: auc={auc} fuera del rango de un coinflip puro"


def test_historical_candidate_recovers_injected_signal(hist_url):
    """Cuando SI hay una diferencia de habilidad real y persistente entre
    equipos (que tambien se manifiesta en sus enfrentamientos directos),
    al menos un candidato debe detectarla via LOSO."""
    engine = historical_db.get_engine(hist_url)
    team_ids = list(range(1, 9))
    team_skill = {t: (0.6 if t <= 2 else -0.2 if t >= 7 else 0.0) for t in team_ids}
    for season, seed, offset in ((2022, 11, 0), (2023, 12, 200), (2024, 13, 400)):
        _seed_games(engine, season, 400, seed=seed, team_skill=team_skill, day_offset=offset)

    records = hca.load_records_with_h2h_candidates(engine, [2022, 2023, 2024])
    result = hca.evaluate_historical_candidates(records)

    aucs = {c: result[c]["individual_stats"]["auc"] for c in hca.HISTORICAL_CANDIDATES if result[c]["individual_stats"]["auc"] is not None}
    assert aucs, "ningun candidato tuvo suficiente cobertura para calcular AUC"
    assert max(aucs.values()) > 0.55, f"ningun candidato detecto la senal inyectada: {aucs}"


def test_run_full_historical_candidate_audit_end_to_end(hist_url):
    engine = historical_db.get_engine(hist_url)
    for season, seed, offset in ((2022, 1, 0), (2023, 2, 200), (2024, 3, 400)):
        _seed_games(engine, season, 150, seed, day_offset=offset)

    result = hca.run_full_historical_candidate_audit([2022, 2023, 2024], hist_url)
    assert result["n_games"] == 450
    assert set(result["phase_descriptive"]["per_candidate"]) == set(hca.HISTORICAL_CANDIDATES)
    assert set(result["phase_loso_comparison"]) == set(hca.HISTORICAL_CANDIDATES)


def test_run_full_historical_candidate_audit_no_games_returns_error(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    result = hca.run_full_historical_candidate_audit([2099], hist_url)
    assert result == {"n_games": 0, "seasons_used": [2099], "error": "no_games_with_full_data"}
