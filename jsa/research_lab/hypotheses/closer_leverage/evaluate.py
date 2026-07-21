"""Evalua la hipotesis Closer Leverage (Modulo 1 del Game Flow Research
Lab): ¿el estado de fatiga/descanso reciente del cerrador (IP en los
`days` dias previos, real via `backfill.py`) aporta informacion adicional
al baseline mas alla de la señal binaria `closer_available` ya wireada en
`engine/pillars/bullpen.py`?

Baseline = `evidence_score_raw` real (produccion actual). Alternativa =
mismo score, recalculado con el advantage de `bullpen` reemplazado por
uno que ademas penaliza fatiga del cerrador -- EXACTAMENTE el mismo
patron que `discriminative_audit.py::shrinkage_sensitivity()`/
`_recompute_starter_bullpen_advantage()` (nunca se llama a
`bullpen.evaluate()` con un `GameSnapshot` sintetico -- se reimplementa la
formula sobre datos reales ya guardados, mismo precedente ya aceptado en
el proyecto para experimentos controlados de un solo pilar)."""

from __future__ import annotations

from dataclasses import asdict

from jsa.config import LEAGUE_AVG_ERA
from jsa.domain.models import SEVEN_PILLARS
from jsa.engine.pillars.base import discretize_diff, shrunk_era
from jsa.historical import calibration
from jsa.historical import db as historical_db
from jsa.historical.discriminative_audit import load_game_pillar_data
from jsa.historical.significance import full_significance_report
from jsa.research_lab.hypothesis_report import HypothesisReport, decide_retention

# Mismos valores que `engine/pillars/bullpen.py` -- duplicados aca a
# proposito, mismo precedente que `discriminative_audit.py`
# (`_recompute_starter_bullpen_advantage`): un experimento controlado de
# un solo pilar reimplementa la formula sobre datos ya guardados, nunca
# llama al pilar de produccion con un snapshot sintetico.
_BULLPEN_UNIT_ERA_RUNS = 0.45
_CLOSER_UNAVAILABLE_PENALTY = 0.30

# Grid de runs-equivalentes por IP reciente del cerrador -- sin calibrar
# contra el proyecto todavia (mismo espiritu honesto que
# `shrinkage_sensitivity`'s `k_values`), acotado para nunca superar el
# penalty de "cerrador lesionado" (fatiga nunca es peor que ausencia).
FATIGUE_PENALTY_PER_IP_GRID: tuple[float, ...] = (0.05, 0.10, 0.15, 0.20)


def load_closer_leverage_records(engine, historical_database_url: str, seasons: list[int]) -> list[dict]:
    """Une `load_game_pillar_data()` (evidence_score_raw/advantages/
    weights/snapshot ya reales) con `historical_closer_leverage` (IP
    reciente del cerrador, real via `backfill.py`) -- juegos sin fila de
    closer leverage para AMBOS equipos quedan afuera (nunca se aproxima
    con 0.0 lo que en realidad no se backfilleo)."""
    records = load_game_pillar_data(engine, seasons)
    team_ids_by_pk: dict[int, dict[str, int]] = {}
    for season in seasons:
        for game in historical_db.games_for_season(engine, season):
            team_ids_by_pk[game["game_pk"]] = {"home": game["home_team_id"], "away": game["away_team_id"]}

    closer_leverage_by_pk_team: dict[tuple[int, int], dict] = {}
    for season in seasons:
        for row in historical_db.closer_leverage_for_season(engine, season):
            closer_leverage_by_pk_team[(row["game_pk"], row["team_id"])] = row

    out = []
    for r in records:
        team_ids = team_ids_by_pk.get(r["game_pk"])
        if team_ids is None:
            continue
        home_row = closer_leverage_by_pk_team.get((r["game_pk"], team_ids["home"]))
        away_row = closer_leverage_by_pk_team.get((r["game_pk"], team_ids["away"]))
        if home_row is None or away_row is None:
            continue
        r["home_closer_recent_ip"] = home_row["closer_recent_ip"]
        r["away_closer_recent_ip"] = away_row["closer_recent_ip"]
        out.append(r)
    return out


def _recompute_bullpen_advantage_with_fatigue(snapshot: dict, home_closer_recent_ip: float | None, away_closer_recent_ip: float | None, fatigue_penalty_per_ip: float, original_bullpen_advantage: int) -> int:
    league_era = snapshot.get("league_avg_era") or LEAGUE_AVG_ERA
    home_bp, away_bp = snapshot.get("home_bullpen_era"), snapshot.get("away_bullpen_era")
    if home_bp is None and away_bp is None:
        return original_bullpen_advantage

    home_e = shrunk_era(home_bp, snapshot.get("home_bullpen_ip_sample") or 0, league_era) if home_bp is not None else league_era
    away_e = shrunk_era(away_bp, snapshot.get("away_bullpen_ip_sample") or 0, league_era) if away_bp is not None else league_era

    home_unavailable_penalty = _CLOSER_UNAVAILABLE_PENALTY if snapshot.get("home_closer_available") is False else 0.0
    away_unavailable_penalty = _CLOSER_UNAVAILABLE_PENALTY if snapshot.get("away_closer_available") is False else 0.0
    home_fatigue_penalty = min(_CLOSER_UNAVAILABLE_PENALTY, fatigue_penalty_per_ip * (home_closer_recent_ip or 0.0))
    away_fatigue_penalty = min(_CLOSER_UNAVAILABLE_PENALTY, fatigue_penalty_per_ip * (away_closer_recent_ip or 0.0))

    # El penalty mas fuerte de los dos manda -- fatiga y ausencia no se
    # SUMAN (un cerrador lesionado ya esta en el techo del penalty,
    # fatigado-pero-disponible usa su propio penalty, nunca ambos).
    home_eff = home_e + max(home_unavailable_penalty, home_fatigue_penalty)
    away_eff = away_e + max(away_unavailable_penalty, away_fatigue_penalty)
    return discretize_diff(away_eff - home_eff, _BULLPEN_UNIT_ERA_RUNS)


def _score_with_fatigue(record: dict, fatigue_penalty_per_ip: float) -> float:
    bullpen_adv = _recompute_bullpen_advantage_with_fatigue(
        record["snapshot"], record["home_closer_recent_ip"], record["away_closer_recent_ip"],
        fatigue_penalty_per_ip, record["advantages"]["bullpen"],
    )
    return sum(record["weights"][p] * (bullpen_adv if p == "bullpen" else record["advantages"][p]) for p in SEVEN_PILLARS)


def evaluate_closer_leverage_hypothesis(records: list[dict], market: str = "moneyline_home") -> dict:
    """Por cada valor del grid: LOSO + significancia completa contra el
    baseline real. Se reporta el resultado del combo con MEJOR (mas
    negativo) delta de Brier del bootstrap -- nunca el que tenga el
    p-valor mas chico por casualidad, mismo criterio que
    `gate_threshold_sweep.py::_best_threshold_combo()` (nunca elegir por
    significancia sola sin mirar la direccion/magnitud real)."""
    baseline_pairs_by_season: dict[int, list[tuple[float, int]]] = {}
    for r in records:
        baseline_pairs_by_season.setdefault(r["season"], []).append((r["evidence_score_raw"], r["home_win"]))
    baseline_loso = calibration.loso_fit_and_score(baseline_pairs_by_season)

    grid_results = {}
    best_penalty, best_delta = None, 0.0
    for penalty in FATIGUE_PENALTY_PER_IP_GRID:
        alt_pairs_by_season: dict[int, list[tuple[float, int]]] = {}
        for r in records:
            score = _score_with_fatigue(r, penalty)
            alt_pairs_by_season.setdefault(r["season"], []).append((score, r["home_win"]))
        alt_loso = calibration.loso_fit_and_score(alt_pairs_by_season)
        significance = full_significance_report(baseline_loso["loso_pairs"], alt_loso["loso_pairs"])
        grid_results[f"fatigue_penalty_per_ip={penalty:g}"] = {
            "loso_brier": alt_loso["loso_brier"], "loso_log_loss": alt_loso["loso_log_loss"],
            "loso_accuracy": alt_loso["loso_accuracy"], "loso_ece": alt_loso["loso_ece"],
            "significance": significance,
        }
        delta = significance["bootstrap"]["delta_brier_mean"] if significance.get("bootstrap") else 0.0
        if delta < best_delta:
            best_delta, best_penalty = delta, penalty

    report = HypothesisReport(
        hypothesis_id="gflab-closer_leverage-v1", module_name="Closer Leverage Engine", market=market,
        n_games=len(records),
    )
    if best_penalty is not None:
        best = grid_results[f"fatigue_penalty_per_ip={best_penalty:g}"]
        report.delta_accuracy = best["loso_accuracy"] - baseline_loso["loso_accuracy"] if baseline_loso["loso_accuracy"] is not None and best["loso_accuracy"] is not None else None
        report.delta_brier = best["loso_brier"] - baseline_loso["loso_brier"] if baseline_loso["loso_brier"] is not None and best["loso_brier"] is not None else None
        report.delta_log_loss = best["loso_log_loss"] - baseline_loso["loso_log_loss"] if baseline_loso["loso_log_loss"] is not None and best["loso_log_loss"] is not None else None
        report.delta_ece = best["loso_ece"] - baseline_loso["loso_ece"] if baseline_loso["loso_ece"] is not None and best["loso_ece"] is not None else None
        report.significance = {"brier": best["significance"]["bootstrap"]}
        retained, reason = decide_retention(report.significance)
        report.retained_in_lab, report.retention_reason = retained, reason

    return {
        "n_games": len(records),
        "baseline": {
            "loso_brier": baseline_loso["loso_brier"], "loso_log_loss": baseline_loso["loso_log_loss"],
            "loso_accuracy": baseline_loso["loso_accuracy"], "loso_ece": baseline_loso["loso_ece"],
        },
        "grid_results": grid_results,
        "best_fatigue_penalty_per_ip": best_penalty,
        "report": report,
    }


def run_closer_leverage_hypothesis(seasons: list[int], historical_database_url: str) -> dict:
    engine = historical_db.get_engine(historical_database_url)
    historical_db.init_historical_storage(engine)
    records = load_closer_leverage_records(engine, historical_database_url, seasons)
    if not records:
        return {"n_games": 0, "seasons_used": seasons, "error": "no_games_with_closer_leverage_backfill"}
    return {"seasons_used": seasons, **evaluate_closer_leverage_hypothesis(records)}
