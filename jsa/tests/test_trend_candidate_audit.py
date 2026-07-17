"""`historical/trend_candidate_audit.py` -- auditoria descriptiva +
comparacion LOSO de los 4 candidatos de forma reciente para Trend (rolling
OPS/ERA 7d/14d). Datos sinteticos deterministicos sobre SQLite real basado
en archivo (nunca `:memory:`, mismo criterio que el resto de los audits).
Nunca red, nunca Postgres real."""

from __future__ import annotations

import datetime
import random

import numpy as np
import pytest
from sklearn.metrics import roc_auc_score

from jsa.domain.models import SEVEN_PILLARS
from jsa.historical import db as historical_db
from jsa.historical import trend_candidate_audit as tca

_BASE_WEIGHTS = {
    "starter": 0.22, "bullpen": 0.25, "offense": 0.20,
    "team_quality": 0.15, "context": 0.08, "trend": 0.05, "historical": 0.05,
}


def _seed_games_with_snapshots(
    engine, season: int, n_games: int, seed: int, *,
    trend_skill: dict | None = None, missing_trend_frac: float = 0.0,
) -> list[tuple[int, int]]:
    """Siembra juegos con snapshot completo, incluyendo los 8 campos
    rolling de Trend. `trend_skill`: dict team_id -> sesgo inyectado en
    `home_team_ops_rolling_7d`/`away_team_ops_rolling_7d` (y espejado en
    ERA con signo invertido) -- permite un sanity check de fuga real,
    igual que `test_elo_recovers_real_team_skill_signal` en
    `test_resolution_audit.py`. `missing_trend_frac`: fraccion de juegos
    sin ventana rolling completa (simula inicio de temporada)."""
    historical_db.init_historical_storage(engine)
    rng = random.Random(seed)
    team_ids = list(range(1, 9))
    trend_skill = trend_skill or {t: 0.0 for t in team_ids}
    # Offset fijo por equipo (independiente de trend_skill/resultado) para que
    # las ventanas de 7d y 14d compartan varianza real de "identidad del
    # equipo" -- en la practica, el OPS rolling de 7d y 14d del MISMO equipo
    # estan correlacionados porque son ventanas solapadas de los mismos
    # juegos recientes, no ruido independiente.
    team_ops_baseline = {t: rng.uniform(-0.04, 0.04) for t in team_ids}
    meta = []
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
        advantages["trend"] = 0  # trend sigue siendo stub en produccion -- SIEMPRE 0, igual que hoy
        evidence_score_raw = sum(_BASE_WEIGHTS[p] * advantages[p] for p in SEVEN_PILLARS)

        trend_edge = trend_skill[home_id] - trend_skill[away_id]
        home_win = 1 if (evidence_score_raw + trend_edge + rng.gauss(0, 0.25)) > 0 else 0
        home_score, away_score = (5, 3) if home_win else (3, 5)
        historical_db.update_game_result(engine, game_pk, home_score, away_score)
        meta.append((game_pk, home_win))

        has_trend_window = rng.random() >= missing_trend_frac
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
        if has_trend_window:
            # `trend_skill` maneja la magnitud del efecto en la formula de
            # home_win (misma escala que team_skill en el sanity check de
            # Elo, ej. 0.6/-0.2); se reescala aca a un rango realista para
            # cada campo (OPS se mueve ~0.01-0.05, ERA ~0.1-0.3) sin perder
            # la correlacion completa con el resultado.
            snapshot_payload.update({
                "home_team_ops_rolling_7d": 0.75 + team_ops_baseline[home_id] + trend_skill[home_id] * 0.08 + rng.gauss(0, 0.02),
                "away_team_ops_rolling_7d": 0.75 + team_ops_baseline[away_id] + trend_skill[away_id] * 0.08 + rng.gauss(0, 0.02),
                "home_team_ops_rolling_14d": 0.75 + team_ops_baseline[home_id] + trend_skill[home_id] * 0.08 + rng.gauss(0, 0.02),
                "away_team_ops_rolling_14d": 0.75 + team_ops_baseline[away_id] + trend_skill[away_id] * 0.08 + rng.gauss(0, 0.02),
                "home_team_era_rolling_7d": 4.30 - trend_skill[home_id] * 0.40 + rng.gauss(0, 0.10),
                "away_team_era_rolling_7d": 4.30 - trend_skill[away_id] * 0.40 + rng.gauss(0, 0.10),
                "home_team_era_rolling_14d": 4.30 - trend_skill[home_id] * 0.40 + rng.gauss(0, 0.10),
                "away_team_era_rolling_14d": 4.30 - trend_skill[away_id] * 0.40 + rng.gauss(0, 0.10),
            })
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
    return meta


@pytest.fixture()
def hist_url(tmp_path):
    return f"sqlite:///{tmp_path}/jsa_trend_candidate_audit_test.db"


@pytest.fixture()
def seeded_records(hist_url):
    engine = historical_db.get_engine(hist_url)
    for season, seed in ((2022, 1), (2023, 2), (2024, 3)):
        _seed_games_with_snapshots(engine, season, 150, seed)
    return tca.load_records_with_trend_candidates(engine, [2022, 2023, 2024])


def test_load_records_with_trend_candidates_shapes(seeded_records):
    assert len(seeded_records) == 450
    r = seeded_records[0]
    assert set(r["trend_candidate_diffs"]) == set(tca.TREND_CANDIDATES)
    assert all(v is not None for v in r["trend_candidate_diffs"].values())


def test_descriptive_audit_covers_all_8_fields(seeded_records):
    result = tca.run_descriptive_audit(seeded_records)
    assert set(result["per_field"]) == set(tca.ROLLING_FIELD_NAMES)
    for field, stats in result["per_field"].items():
        assert stats["coverage_pct"] == 1.0
        assert stats["mean"] is not None
        assert stats["min"] <= stats["median"] <= stats["max"]
    # ops_rolling_7d y ops_rolling_14d deberian estar bastante correlacionados
    # (mismo team_skill inyectado, distinta ventana) -- valida que la
    # correlacion cruzada se calcula de verdad, no un placeholder.
    key = "home_team_ops_rolling_7d__vs__home_team_ops_rolling_14d"
    assert key in result["cross_field_correlations"]
    assert result["cross_field_correlations"][key] > 0.3


def test_descriptive_audit_reports_partial_coverage(hist_url):
    engine = historical_db.get_engine(hist_url)
    _seed_games_with_snapshots(engine, 2022, 200, seed=7, missing_trend_frac=0.3)
    records = tca.load_records_with_trend_candidates(engine, [2022])
    result = tca.run_descriptive_audit(records)
    for stats in result["per_field"].values():
        assert 0.55 <= stats["coverage_pct"] <= 0.85


def test_evaluate_trend_candidates_shape(seeded_records):
    result = tca.evaluate_trend_candidates(seeded_records)
    assert set(result) == set(tca.TREND_CANDIDATES)
    for entry in result.values():
        assert "auc" in entry["individual_stats"]
        assert entry["loso_if_substituted"]["loso_brier"] is not None
        assert set(entry["loso_if_substituted"]["per_season_metrics"]) == {2022, 2023, 2024}
        assert set(entry["current_trend_per_season_metrics"]) == {2022, 2023, 2024}
        assert entry["bootstrap_ci_delta_brier_vs_actual_trend"] is not None


def test_trend_candidate_no_signal_on_pure_coinflip(hist_url):
    """Sanity check anti-fuga: si el resultado es una moneda pura sin
    ninguna relacion con el team_skill inyectado en las rolling stats, el
    AUC de cada candidato debe quedar cerca de 0.5 -- si no, hay una fuga
    real en el calculo del diff."""
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
        historical_db.persist_historical_snapshot(
            engine, season=season, game_pk=game_pk, game_date=game_date,
            snapshot_hash=f"hash-{game_pk}",
            snapshot_payload={
                "league_avg_era": 4.30, "league_avg_ops": 0.75,
                "home_team_ops_rolling_7d": 0.75 + rng.gauss(0, 0.05), "away_team_ops_rolling_7d": 0.75 + rng.gauss(0, 0.05),
                "home_team_ops_rolling_14d": 0.75 + rng.gauss(0, 0.05), "away_team_ops_rolling_14d": 0.75 + rng.gauss(0, 0.05),
                "home_team_era_rolling_7d": 4.30 + rng.gauss(0, 0.30), "away_team_era_rolling_7d": 4.30 + rng.gauss(0, 0.30),
                "home_team_era_rolling_14d": 4.30 + rng.gauss(0, 0.30), "away_team_era_rolling_14d": 4.30 + rng.gauss(0, 0.30),
            },
        )
        historical_db.persist_historical_report(
            engine, run_id="run-2022", season=season, game_pk=game_pk, game_date=game_date,
            report_payload={
                "manifest_status": "valid", "evidence_score_raw": 0.0,
                "pillar_advantages": [
                    {"pillar": p, "advantage": 0, "explanation": "sintetico", "pillar_contract_version": f"{p}@1.0.0"}
                    for p in SEVEN_PILLARS
                ],
                "final_weights": dict(_BASE_WEIGHTS),
            },
        )

    records = tca.load_records_with_trend_candidates(engine, [season])
    for candidate in tca.TREND_CANDIDATES:
        diffs = np.array([r["trend_candidate_diffs"][candidate] for r in records])
        y = np.array([1 if w else 0 for _, w in meta])
        auc = roc_auc_score(y, diffs)
        assert 0.40 <= auc <= 0.60, f"{candidate}: auc={auc} fuera del rango de un coinflip puro"


def test_trend_candidate_recovers_injected_signal(hist_url):
    """Cuando SI hay una relacion real y persistente entre la forma
    reciente (rolling OPS/ERA) y el resultado, al menos un candidato debe
    detectarla: AUC claramente por encima de 0.5 y la sustitucion LOSO
    debe mostrar una mejora significativa (bootstrap CI) vs. Trend=0."""
    engine = historical_db.get_engine(hist_url)
    team_ids = list(range(1, 9))
    trend_skill = {t: (0.6 if t <= 2 else -0.2 if t >= 7 else 0.0) for t in team_ids}
    for season, seed in ((2022, 11), (2023, 12), (2024, 13)):
        _seed_games_with_snapshots(engine, season, 400, seed=seed, trend_skill=trend_skill)

    records = tca.load_records_with_trend_candidates(engine, [2022, 2023, 2024])
    result = tca.evaluate_trend_candidates(records)

    aucs = {c: result[c]["individual_stats"]["auc"] for c in tca.TREND_CANDIDATES}
    assert max(aucs.values()) > 0.55, f"ningun candidato detecto la senal inyectada: {aucs}"

    best = max(aucs, key=lambda c: aucs[c])
    ci = result[best]["bootstrap_ci_delta_brier_vs_actual_trend"]
    assert ci["delta_brier_mean"] < 0, "el mejor candidato deberia mostrar una mejora (delta_brier negativo) vs Trend=0"


def test_run_full_trend_candidate_audit_end_to_end(hist_url):
    engine = historical_db.get_engine(hist_url)
    for season, seed in ((2022, 1), (2023, 2), (2024, 3)):
        _seed_games_with_snapshots(engine, season, 150, seed)

    result = tca.run_full_trend_candidate_audit([2022, 2023, 2024], hist_url)
    assert result["n_games"] == 450
    assert set(result["phase_descriptive"]["per_field"]) == set(tca.ROLLING_FIELD_NAMES)
    assert set(result["phase_loso_comparison"]) == set(tca.TREND_CANDIDATES)


def test_run_full_trend_candidate_audit_no_games_returns_error(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    result = tca.run_full_trend_candidate_audit([2099], hist_url)
    assert result == {"n_games": 0, "seasons_used": [2099], "error": "no_games_with_full_data"}
