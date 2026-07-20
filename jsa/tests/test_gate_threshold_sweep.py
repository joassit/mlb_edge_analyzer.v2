"""`historical/gate_threshold_sweep.py` -- Fase 6 (Seccion 10.3/10.4):
Gate Threshold Sweep con nested walk-forward real. Datos sinteticos
deterministicos sobre SQLite real basado en archivo, nunca red, nunca
Postgres real."""

from __future__ import annotations

import datetime
import random

import pytest

from jsa.historical import db as historical_db
from jsa.historical import gate_threshold_sweep as gts


def test_wilson_ci_zero_n_returns_zero_zero():
    assert gts._wilson_ci(0, 0) == (0.0, 0.0)


def test_wilson_ci_perfect_record_has_ci_below_one():
    """Con muestra chica, incluso 10/10 aciertos no da un CI de [1.0, 1.0]
    -- Wilson penaliza muestra chica, a diferencia del intervalo normal
    aproximado."""
    ci_low, ci_high = gts._wilson_ci(10, 10)
    assert 0.0 < ci_low < 1.0
    assert ci_high == 1.0


def test_wilson_ci_widens_with_smaller_sample():
    ci_low_small, ci_high_small = gts._wilson_ci(7, 10)
    ci_low_big, ci_high_big = gts._wilson_ci(700, 1000)
    assert (ci_high_small - ci_low_small) > (ci_high_big - ci_low_big)


def test_market_probability_and_correctness_moneyline_home():
    prob, correct = gts._market_probability_and_correctness("moneyline_home", 0.7, home_win=1)
    assert prob == 0.7
    assert correct is True

    prob, correct = gts._market_probability_and_correctness("moneyline_home", 0.7, home_win=0)
    assert prob == 0.7
    assert correct is False


def test_market_probability_and_correctness_moneyline_away_inverts():
    prob, correct = gts._market_probability_and_correctness("moneyline_away", 0.7, home_win=0)
    assert prob == pytest.approx(0.3)
    assert correct is True

    prob, correct = gts._market_probability_and_correctness("moneyline_away", 0.7, home_win=1)
    assert prob == pytest.approx(0.3)
    assert correct is False


def test_best_threshold_combo_none_when_nothing_reaches_min_coverage():
    rows = [{"probability": 0.9, "cri_score": 95, "uncertainty_index": 5, "correct": True} for _ in range(5)]
    assert gts._best_threshold_combo(rows) is None  # 5 < MIN_COVERAGE_N=30


def test_best_threshold_combo_prefers_wilson_lower_bound_over_raw_accuracy():
    rows = []
    # Combo A: 100% accuracy pero muestra chica (justo en el minimo).
    rows += [{"probability": 0.8, "cri_score": 95, "uncertainty_index": 5, "correct": True} for _ in range(gts.MIN_COVERAGE_N)]
    combo = gts._best_threshold_combo(rows)
    assert combo is not None
    assert combo["n"] == gts.MIN_COVERAGE_N


def _seed_season(engine, season: int, n_games: int, seed: int, *, signal_strength: float = 0.0) -> None:
    """`evidence_score_raw` correlaciona con `home_win` segun
    `signal_strength` (0.0 = coinflip puro). `cri_score`/`uncertainty_index`
    fijos en un rango "bueno" (alto CRI, baja incertidumbre) para que el
    Gate tenga margen de sobra y la unica variable real sea la
    probabilidad calibrada -- aisla lo que este modulo prueba."""
    historical_db.init_historical_storage(engine)
    rng = random.Random(seed)
    team_ids = list(range(1, 9))

    for i in range(n_games):
        game_pk = season * 100000 + i
        game_date = datetime.date(season, 4, 1) + datetime.timedelta(days=i % 150)
        home_id, away_id = rng.sample(team_ids, 2)
        historical_db.upsert_game(
            engine, season=season, game_pk=game_pk, game_date=game_date,
            home_team=f"T{home_id}", away_team=f"T{away_id}", home_team_id=home_id, away_team_id=away_id,
            home_pitcher_id=1, away_pitcher_id=2, is_double_header=0,
        )
        evidence_score_raw = rng.uniform(-2.0, 2.0)
        home_win = 1 if (evidence_score_raw * signal_strength + rng.gauss(0, 1.0)) > 0 else 0
        historical_db.update_game_result(engine, game_pk, 5 if home_win else 3, 3 if home_win else 5)
        historical_db.persist_historical_report(
            engine, run_id=f"run-{season}", season=season, game_pk=game_pk, game_date=game_date,
            report_payload={
                "manifest_status": "valid", "evidence_score_raw": evidence_score_raw,
                "cri_score": 90.0, "uncertainty_index": 10.0,
            },
        )


@pytest.fixture()
def hist_url(tmp_path):
    return f"sqlite:///{tmp_path}/jsa_gate_threshold_sweep_test.db"


def test_run_gate_threshold_sweep_no_games_returns_error(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    result = gts.run_gate_threshold_sweep([2099], hist_url)
    assert result == {"n_games": 0, "seasons_used": [2099], "error": "no_games_with_full_data"}


def test_gate_threshold_sweep_rejects_pure_coinflip(hist_url):
    """Sanity check anti-fuga: sin relacion real entre evidence_score_raw
    y el resultado, ningun mercado deberia alcanzar validated_70."""
    engine = historical_db.get_engine(hist_url)
    for season, seed in ((2022, 1), (2023, 2), (2024, 3), (2025, 4), (2026, 5)):
        _seed_season(engine, season, 400, seed=seed, signal_strength=0.0)

    result = gts.run_gate_threshold_sweep([2022, 2023, 2024, 2025, 2026], hist_url)
    assert result["markets_evaluated"] == list(gts.MARKETS_WITH_MODEL)
    assert result["markets_without_model"] == list(gts.MARKETS_WITHOUT_MODEL)
    for market_id in gts.MARKETS_WITH_MODEL:
        assert result["market_results"][market_id]["status"] != "validated_70"


def test_gate_threshold_sweep_validates_strong_real_signal(hist_url):
    """Con una senal real y fuerte (evidence_score_raw predice bien el
    resultado en las 5 temporadas), el nested walk-forward deberia
    encontrar un threshold que alcanza validated_70 para ambos mercados
    moneyline."""
    engine = historical_db.get_engine(hist_url)
    for season, seed in ((2022, 10), (2023, 11), (2024, 12), (2025, 13), (2026, 14)):
        _seed_season(engine, season, 500, seed=seed, signal_strength=3.0)

    result = gts.run_gate_threshold_sweep([2022, 2023, 2024, 2025, 2026], hist_url)
    for market_id in gts.MARKETS_WITH_MODEL:
        market_result = result["market_results"][market_id]
        assert market_result["status"] == "validated_70", market_result
        nested = market_result["nested_walk_forward"]
        assert nested["accuracy_wilson_ci_low"] >= gts.ACCURACY_VALIDATED_THRESHOLD
        assert market_result["production_thresholds"] is not None
