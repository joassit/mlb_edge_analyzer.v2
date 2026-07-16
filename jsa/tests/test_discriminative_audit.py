"""`historical/discriminative_audit.py` -- auditoria de poder discriminativo
del Evidence Score sobre datos sinteticos deterministicos (SQLite real
basado en archivo, nunca `:memory:` -- `run_full_audit()` abre su PROPIO
engine desde la URL, mismo criterio que `test_calibration.py`). Nunca red,
nunca Postgres real. `optimizer_maxiter`/`optimizer_popsize` chicos en
todos los tests para mantener la suite rapida -- la Fase 4 solo necesita
demostrar que corre y devuelve pesos validos, no converger de verdad."""

from __future__ import annotations

import datetime
import random

import pytest

from jsa.domain.models import SEVEN_PILLARS
from jsa.historical import db as historical_db
from jsa.historical import discriminative_audit as audit

_BASE_WEIGHTS = {
    "starter": 0.22, "bullpen": 0.25, "offense": 0.20,
    "team_quality": 0.15, "context": 0.08, "trend": 0.05, "historical": 0.05,
}


def _seed_games(engine, season: int, n_games: int, seed: int) -> None:
    """Siembra `n_games` juegos con advantages/pesos/snapshot variados y
    una relacion real (con ruido) entre `starter` y el resultado -- para
    que ablacion/permutation-importance tengan algo real que detectar."""
    historical_db.init_historical_storage(engine)
    rng = random.Random(seed)
    for i in range(n_games):
        game_pk = season * 100000 + i
        game_date = datetime.date(season, 4, 1) + datetime.timedelta(days=i % 150)
        historical_db.upsert_game(
            engine, season=season, game_pk=game_pk, game_date=game_date,
            home_team="Home", away_team="Away", home_team_id=1, away_team_id=2,
            home_pitcher_id=100, away_pitcher_id=200, is_double_header=0,
        )
        advantages = {p: rng.choice([-2, -1, 0, 1, 2]) for p in SEVEN_PILLARS}
        evidence_score_raw = sum(_BASE_WEIGHTS[p] * advantages[p] for p in SEVEN_PILLARS)
        home_win = 1 if (evidence_score_raw + rng.gauss(0, 0.25)) > 0 else 0
        home_score, away_score = (5, 3) if home_win else (3, 5)
        historical_db.update_game_result(engine, game_pk, home_score, away_score)

        home_starter_xera = 3.0 + rng.random() * 2.0
        away_starter_xera = 3.0 + rng.random() * 2.0
        home_bullpen_era = 3.0 + rng.random() * 2.0
        away_bullpen_era = 3.0 + rng.random() * 2.0
        snapshot_payload = {
            "league_avg_era": 4.30,
            "home_starter_xera": home_starter_xera, "away_starter_xera": away_starter_xera,
            "home_starter_ip_sample": 30.0 + rng.random() * 40.0,
            "away_starter_ip_sample": 30.0 + rng.random() * 40.0,
            "home_bullpen_era": home_bullpen_era, "away_bullpen_era": away_bullpen_era,
            "home_bullpen_ip_sample": 50.0 + rng.random() * 80.0,
            "away_bullpen_ip_sample": 50.0 + rng.random() * 80.0,
            "home_closer_available": True, "away_closer_available": True,
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
    return f"sqlite:///{tmp_path}/jsa_discriminative_audit_test.db"


@pytest.fixture()
def seeded_records(hist_url):
    engine = historical_db.get_engine(hist_url)
    for season, seed in ((2022, 1), (2023, 2), (2024, 3)):
        _seed_games(engine, season, 80, seed)
    return audit.load_game_pillar_data(engine, [2022, 2023, 2024])


def test_load_game_pillar_data_shapes(seeded_records):
    assert len(seeded_records) == 240
    r = seeded_records[0]
    assert set(r["advantages"]) == set(SEVEN_PILLARS)
    assert set(r["weights"]) == set(SEVEN_PILLARS)
    assert r["home_win"] in (0, 1)
    assert "home_starter_xera" in r["snapshot"]


def test_load_game_pillar_data_skips_games_without_result(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    historical_db.upsert_game(
        engine, season=2022, game_pk=1, game_date=datetime.date(2022, 4, 1),
        home_team="H", away_team="A", home_team_id=1, away_team_id=2,
        home_pitcher_id=1, away_pitcher_id=2, is_double_header=0,
    )
    pillar_advantages = [{"pillar": p, "advantage": 0, "explanation": "x", "pillar_contract_version": "x@1.0.0"} for p in SEVEN_PILLARS]
    historical_db.persist_historical_report(
        engine, run_id="run", season=2022, game_pk=1, game_date=datetime.date(2022, 4, 1),
        report_payload={"manifest_status": "valid", "evidence_score_raw": 0.0, "pillar_advantages": pillar_advantages, "final_weights": _BASE_WEIGHTS},
    )
    assert audit.load_game_pillar_data(engine, [2022]) == []


def test_pillar_individual_stats_shape_and_ranges(seeded_records):
    baseline_loso = audit.calibration.loso_fit_and_score(audit._baseline_pairs_by_season(seeded_records))
    stats = audit.pillar_individual_stats(seeded_records, baseline_loso)
    assert set(stats) == set(SEVEN_PILLARS)
    for pillar in SEVEN_PILLARS:
        s = stats[pillar]
        assert 0.0 <= s["auc"] <= 1.0
        assert sum(s["distribution"].values()) == 240
        assert s["permutation_importance"]["n_repeats"] == 3


def test_pillar_correlation_matrices_diagonal(seeded_records):
    result = audit.pillar_correlation_matrices(seeded_records)
    assert result["pillars_order"] == list(SEVEN_PILLARS)
    n = len(SEVEN_PILLARS)
    for i in range(n):
        assert result["pearson"][i][i] == pytest.approx(1.0, abs=1e-6)
        assert result["spearman"][i][i] == pytest.approx(1.0, abs=1e-6)


def test_ablation_analysis_has_all_scenarios(seeded_records):
    baseline_loso = audit.calibration.loso_fit_and_score(audit._baseline_pairs_by_season(seeded_records))
    result = audit.ablation_analysis(seeded_records, baseline_loso)
    assert "todos_los_pilares" in result
    for pillar in SEVEN_PILLARS:
        entry = result[f"sin_{pillar}"]
        assert entry["classification"] in ("imprescindible", "util", "neutro", "perjudicial", "no_demostrado")
        assert entry["loso_brier"] is not None


def test_optimize_weights_returns_valid_simplex(seeded_records):
    result = audit.optimize_weights(seeded_records, seed=0, maxiter=3, popsize=4)
    optimized = result["optimized_weights"]
    assert set(optimized) == set(SEVEN_PILLARS)
    assert all(w >= 0 for w in optimized.values())
    assert sum(optimized.values()) == pytest.approx(1.0, abs=1e-6)
    assert result["current_weights"] == pytest.approx(_BASE_WEIGHTS)
    assert "warning" in result  # marca explicita de sesgo de seleccion -- ver optimize_weights_nested
    assert result["optimizer_n_function_evaluations"] > 0


def test_optimize_weights_nested_has_no_selection_bias_by_construction(seeded_records):
    """No hay forma directa de medir "fuga de informacion" en una
    aserción -- lo que se verifica aqui es la propiedad estructural que
    la evita: cada temporada externa produce SU PROPIO vector de pesos
    (optimizado sin verla), y las metricas finales se agregan sobre
    predicciones donde el modelo (pesos + curva) nunca vio esa
    temporada. Tambien verifica que cada fold registra su propio costo
    (n_function_evaluations, tiempo) -- pedido explicito del usuario para
    poder detectar folds que convergen distinto a los demas."""
    result = audit.optimize_weights_nested(seeded_records, seed=0, maxiter=3, popsize=4)
    assert result["n_outer_folds"] == 3
    assert set(result["per_season_optimized_weights"]) == {2022, 2023, 2024}
    for entry in result["per_season_optimized_weights"].values():
        w = entry["optimized_weights"]
        assert set(w) == set(SEVEN_PILLARS)
        assert all(v >= 0 for v in w.values())
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-6)
        assert entry["optimizer_n_function_evaluations"] > 0
        assert entry["fold_seconds"] >= 0
    assert result["current_metrics_nested"]["brier"] is not None
    assert result["optimized_metrics_nested"]["brier"] is not None
    assert isinstance(result["generalizes"], bool)


def test_score_distribution_matches_data(seeded_records):
    result = audit.score_distribution(seeded_records)
    assert result["n"] == 240
    scores = [r["evidence_score_raw"] for r in seeded_records]
    assert result["mean"] == pytest.approx(sum(scores) / len(scores), abs=1e-9)
    assert result["min"] <= result["mean"] <= result["max"]


def test_separability_analysis_won_higher_than_lost(seeded_records):
    result = audit.separability_analysis(seeded_records)
    assert result["mean_won"] > result["mean_lost"]
    assert result["cohens_d"] > 0
    assert 0.0 <= result["overlap_coefficient"] <= 1.0


def test_performance_curves_shapes(seeded_records):
    baseline_loso = audit.calibration.loso_fit_and_score(audit._baseline_pairs_by_season(seeded_records))
    result = audit.performance_curves(baseline_loso["loso_pairs"])
    assert 0.0 <= result["roc_auc"] <= 1.0
    assert len(result["roc_curve"]["fpr"]) == 21
    assert len(result["lift_by_decile"]) == 10
    assert len(result["cumulative_gain_by_decile"]) == 10
    assert len(result["reliability_diagram"]) == 15


def test_shrinkage_sensitivity_has_all_k_values(seeded_records):
    baseline_loso = audit.calibration.loso_fit_and_score(audit._baseline_pairs_by_season(seeded_records))
    result = audit.shrinkage_sensitivity(seeded_records, baseline_loso, k_values=(0.0, 20.0, 60.0))
    assert set(result) == {"k_ip=0", "k_ip=20", "k_ip=60"}
    for entry in result.values():
        assert entry["loso_brier"] is not None


def test_run_full_audit_end_to_end(hist_url):
    engine = historical_db.get_engine(hist_url)
    for season, seed in ((2022, 1), (2023, 2), (2024, 3)):
        _seed_games(engine, season, 80, seed)

    result = audit.run_full_audit(
        [2022, 2023, 2024], hist_url,
        optimizer_maxiter=3, optimizer_popsize=4, nested_optimizer_maxiter=3, nested_optimizer_popsize=4,
    )

    assert result["n_games"] == 240
    for key in (
        "run_metadata", "baseline", "phase1_pillar_stats", "phase2_correlations", "phase3_ablation",
        "phase4_weight_optimization", "phase4_weight_optimization_nested", "phase5_distribution",
        "phase6_separability", "phase7_curves", "phase8_shrinkage", "phase_timings_seconds", "phase_peak_rss_kb",
    ):
        assert key in result

    # Versionado del commit/config -- pedido explicito del usuario antes
    # de correr contra Postgres real.
    metadata = result["run_metadata"]
    assert metadata["config"]["seasons_requested"] == [2022, 2023, 2024]
    assert metadata["config"]["base_pillar_weights"] == _BASE_WEIGHTS
    assert metadata["generated_at_utc"]

    # Timing/memoria por fase -- todas las 8 fases quedan registradas.
    for phase_name in (
        "phase1_pillar_stats", "phase2_correlations", "phase3_ablation",
        "phase4_weight_optimization", "phase4_weight_optimization_nested",
        "phase5_distribution", "phase6_separability", "phase7_curves", "phase8_shrinkage",
    ):
        assert result["phase_timings_seconds"][phase_name] >= 0
        assert result["phase_peak_rss_kb"][phase_name] > 0


def test_run_full_audit_no_games_returns_error(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    result = audit.run_full_audit([2099], hist_url, optimizer_maxiter=3, optimizer_popsize=4)
    assert result == {"n_games": 0, "seasons_used": [2099], "error": "no_games_with_full_data"}
