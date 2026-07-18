"""`historical/statcast_candidate_audit.py` -- comparacion LOSO de las 4
hipotesis Statcast (H1-H4). Datos sinteticos deterministicos sobre
SQLite real basado en archivo (nunca `:memory:`). Nunca red, nunca
Postgres real."""

from __future__ import annotations

import datetime
import random

import numpy as np
import pytest
from sklearn.metrics import roc_auc_score

from jsa.domain.models import SEVEN_PILLARS
from jsa.historical import db as historical_db
from jsa.historical import statcast_candidate_audit as sca

_BASE_WEIGHTS = {
    "starter": 0.22, "bullpen": 0.25, "offense": 0.20,
    "team_quality": 0.15, "context": 0.08, "trend": 0.05, "historical": 0.05,
}


def _starter_id(team_id: int) -> int:
    return team_id * 10 + 1


def _bullpen_id(team_id: int) -> int:
    return team_id * 10 + 2


def _seed_season(engine, season: int, n_games: int, seed: int, *, team_skill: dict | None = None) -> None:
    historical_db.init_historical_storage(engine)
    rng = random.Random(seed)
    team_ids = list(range(1, 9))
    team_skill = team_skill or {t: 0.0 for t in team_ids}
    statcast_events = []

    for i in range(n_games):
        game_pk = season * 100000 + i
        game_date = datetime.date(season, 4, 1) + datetime.timedelta(days=i % 150)
        home_id, away_id = rng.sample(team_ids, 2)
        historical_db.upsert_game(
            engine, season=season, game_pk=game_pk, game_date=game_date,
            home_team=f"T{home_id}", away_team=f"T{away_id}", home_team_id=home_id, away_team_id=away_id,
            home_pitcher_id=_starter_id(home_id), away_pitcher_id=_starter_id(away_id), is_double_header=0,
        )
        advantages = {p: rng.choice([-2, -1, 0, 1, 2]) for p in SEVEN_PILLARS}
        advantages["trend"] = 0
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

        # Top (visitante batea, home pitchea) -- 1 evento vs abridor, 1 vs bullpen.
        statcast_events.append(_event(game_pk, game_date, i * 10 + 1, "Top", batter=away_id * 100, pitcher=_starter_id(home_id),
                                       xwoba=0.30 + team_skill[away_id] * 0.08 + rng.gauss(0, 0.03),
                                       launch_speed=88.0 + team_skill[away_id] * 5.0 + rng.gauss(0, 3.0)))
        statcast_events.append(_event(game_pk, game_date, i * 10 + 2, "Top", batter=away_id * 100, pitcher=_bullpen_id(home_id),
                                       xwoba=0.30 + team_skill[away_id] * 0.08 + rng.gauss(0, 0.03),
                                       launch_speed=88.0 + team_skill[away_id] * 5.0 + rng.gauss(0, 3.0)))
        # Bot (local batea, visitante pitchea) -- 1 evento vs abridor, 1 vs bullpen.
        statcast_events.append(_event(game_pk, game_date, i * 10 + 3, "Bot", batter=home_id * 100, pitcher=_starter_id(away_id),
                                       xwoba=0.30 + team_skill[home_id] * 0.08 + rng.gauss(0, 0.03),
                                       launch_speed=88.0 + team_skill[home_id] * 5.0 + rng.gauss(0, 3.0)))
        statcast_events.append(_event(game_pk, game_date, i * 10 + 4, "Bot", batter=home_id * 100, pitcher=_bullpen_id(away_id),
                                       xwoba=0.30 + team_skill[home_id] * 0.08 + rng.gauss(0, 0.03),
                                       launch_speed=88.0 + team_skill[home_id] * 5.0 + rng.gauss(0, 3.0)))

    historical_db.bulk_insert_statcast_events(engine, season, statcast_events)


def _event(game_pk, game_date, at_bat_number, inning_topbot, *, batter, pitcher, xwoba, launch_speed):
    return {
        "game_pk": game_pk, "game_date": game_date, "at_bat_number": at_bat_number, "pitch_number": 1,
        "inning_topbot": inning_topbot, "batter_id": batter, "pitcher_id": pitcher,
        "launch_speed": launch_speed, "xwoba": xwoba,
    }


@pytest.fixture()
def hist_url(tmp_path):
    return f"sqlite:///{tmp_path}/jsa_statcast_candidate_audit_test.db"


@pytest.fixture()
def seeded_records(hist_url):
    engine = historical_db.get_engine(hist_url)
    for season, seed in ((2022, 1), (2023, 2), (2024, 3)):
        _seed_season(engine, season, 150, seed)
    return sca.load_records_with_statcast_candidates(engine, [2022, 2023, 2024])


def test_load_records_with_statcast_candidates_shapes(seeded_records):
    assert len(seeded_records) == 450
    r = seeded_records[0]
    assert set(r["statcast_candidate_diffs"]) == set(sca.ALL_HYPOTHESES)


def test_first_game_of_season_has_no_offense_history_yet(hist_url):
    """Sanity check point-in-time: el primer juego de la temporada para
    CUALQUIER equipo no puede tener historial de xwOBA previo -- h1 debe
    ser None (nunca se filtra el resultado del propio juego)."""
    engine = historical_db.get_engine(hist_url)
    _seed_season(engine, 2022, 50, seed=7)
    candidates = sca.compute_statcast_candidates(engine, [2022])
    games = sorted(historical_db.games_for_season(engine, 2022), key=lambda g: (g["game_date"], g["game_pk"]))
    first_game = games[0]
    entry = candidates[(2022, first_game["game_pk"])]
    assert entry["h1_offense_xwoba"] is None
    assert entry["h2_starter_xwoba_allowed"] is None
    assert entry["h3_bullpen_xwoba_allowed"] is None


def test_statcast_candidates_no_signal_on_pure_coinflip(hist_url):
    """Sanity check anti-fuga: sin relacion real entre team_skill y el
    resultado, el AUC de cada hipotesis con cobertura suficiente debe
    quedar cerca de 0.5."""
    engine = historical_db.get_engine(hist_url)
    _seed_season(engine, 2022, 300, seed=99)  # team_skill=None -> todos en 0.0, home_win es ruido puro
    records = sca.load_records_with_statcast_candidates(engine, [2022])
    y = np.array([r["home_win"] for r in records])
    for hyp in sca.ALL_HYPOTHESES:
        diffs = [r["statcast_candidate_diffs"][hyp] for r in records]
        mask = np.array([d is not None for d in diffs])
        if mask.sum() < 30:
            continue
        values = np.array([d for d in diffs if d is not None])
        auc = roc_auc_score(y[mask], values)
        assert 0.35 <= auc <= 0.65, f"{hyp}: auc={auc} fuera del rango de un coinflip puro"


def test_statcast_candidates_recover_injected_signal(hist_url):
    """Cuando SI hay una diferencia de habilidad real y persistente
    (reflejada tanto en el resultado como en las metricas Statcast), al
    menos una hipotesis debe detectarla via LOSO."""
    engine = historical_db.get_engine(hist_url)
    team_ids = list(range(1, 9))
    team_skill = {t: (0.6 if t <= 2 else -0.2 if t >= 7 else 0.0) for t in team_ids}
    for season, seed in ((2022, 11), (2023, 12), (2024, 13)):
        _seed_season(engine, season, 400, seed=seed, team_skill=team_skill)

    records = sca.load_records_with_statcast_candidates(engine, [2022, 2023, 2024])
    result = sca.evaluate_statcast_candidates(records)
    aucs = {h: result[h]["individual_stats"]["auc"] for h in sca.ALL_HYPOTHESES if result[h]["individual_stats"]["auc"] is not None}
    assert aucs, "ninguna hipotesis tuvo cobertura suficiente para AUC"
    assert max(aucs.values()) > 0.55, f"ninguna hipotesis detecto la senal inyectada: {aucs}"


def test_evaluate_statcast_candidates_shape(seeded_records):
    result = sca.evaluate_statcast_candidates(seeded_records)
    assert set(result) == set(sca.ALL_HYPOTHESES)
    for hyp, entry in result.items():
        assert entry["target_pillar"] == sca._TARGET_PILLAR[hyp]
        assert "auc" in entry["individual_stats"]
        assert entry["loso_if_substituted"]["loso_brier"] is not None
        assert set(entry["loso_if_substituted"]["per_season_metrics"]) == {2022, 2023, 2024}
        assert entry["bootstrap_ci_delta_brier_vs_current"] is not None


def test_run_full_statcast_candidate_audit_end_to_end(hist_url):
    engine = historical_db.get_engine(hist_url)
    for season, seed in ((2022, 1), (2023, 2), (2024, 3)):
        _seed_season(engine, season, 150, seed)

    result = sca.run_full_statcast_candidate_audit([2022, 2023, 2024], hist_url)
    assert result["n_games"] == 450
    assert set(result["phase_loso_comparison"]) == set(sca.ALL_HYPOTHESES)


def test_run_full_statcast_candidate_audit_no_games_returns_error(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    result = sca.run_full_statcast_candidate_audit([2099], hist_url)
    assert result == {"n_games": 0, "seasons_used": [2099], "error": "no_games_with_full_data"}
