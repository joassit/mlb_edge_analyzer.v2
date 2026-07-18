"""Comparacion LOSO de las 4 hipotesis Statcast (H1-H4) definidas en
`jsa/docs/statcast_integration_design.md`, Etapa 2 -- ingesta minima ya
completada por `statcast_ingestion.py` (solo `launch_speed` +
`estimated_woba_using_speedangle` por evento de bateo, liga completa).

Point-in-time-safe, dia-batched, RESETEADO POR TEMPORADA (mismo criterio
que `offense`/`starter`/`bullpen` ya usan -- son estadisticas "de esta
temporada", no acumulan entre temporadas como si hicieran head-to-head).
Ningun estado se lee antes de que TODOS los juegos de ese dia hayan sido
procesados en el paso de lectura -- misma disciplina anti-fuga que
`compute_elo_and_pythagorean()`/`compute_head_to_head_history()`.

- H1: xwOBA de equipo (ofensiva) acumulado en la temporada -- sustituto
  candidato del insumo de `offense` (hoy OPS).
  Ojo: la ausencia de `home_ops_pa_sample` en esta comparacion se debe a
  que aca se recalcula desde cero con datos Statcast, no con OPS.
- H2: xwOBA permitido acumulado del ABRIDOR especifico de ese juego
  (identificado via `historical_game.home_pitcher_id`/`away_pitcher_id`)
  -- sustituto candidato del insumo de `starter` (hoy ERA con shrinkage).
- H3: xwOBA permitido acumulado del BULLPEN de equipo (apariciones NO del
  abridor) -- sustituto candidato del insumo de `bullpen`.
- H4: hard-hit rate de equipo (ofensiva) en ventana rolling 7d/14d --
  candidato de Trend, comparado contra Trend=0 (mismo protocolo que
  `trend_candidate_audit.py`), NO contra `offense`.

Cada hipotesis se evalua sustituyendo UNICAMENTE el pilar correspondiente
(z-scoreado, mismo peso que produccion) y comparando via bootstrap CI
contra el valor REAL de produccion de ese pilar (H1-H3) o contra
Trend=0 (H4) -- nunca contra un pilar en 0 para H1-H3, porque
starter/bullpen/offense ya son "imprescindibles" (ver diagnostico del
techo del modelo, ROADMAP.md)."""

from __future__ import annotations

import logging

import numpy as np
from scipy.stats import ks_2samp, pearsonr
from sklearn.feature_selection import mutual_info_classif
from sklearn.metrics import roc_auc_score

from jsa.domain.models import SEVEN_PILLARS
from jsa.historical import calibration
from jsa.historical import db as historical_db
from jsa.historical.discriminative_audit import _paired_bootstrap_ci, load_game_pillar_data

logger = logging.getLogger("jsa.historical")

HARD_HIT_THRESHOLD_MPH = 95.0
ROLLING_WINDOW_DAYS = {"h4_hard_hit_rolling_7d": 7, "h4_hard_hit_rolling_14d": 14}

H1_H3_HYPOTHESES = ("h1_offense_xwoba", "h2_starter_xwoba_allowed", "h3_bullpen_xwoba_allowed")
H4_HYPOTHESES = ("h4_hard_hit_rolling_7d", "h4_hard_hit_rolling_14d")
ALL_HYPOTHESES = H1_H3_HYPOTHESES + H4_HYPOTHESES

_TARGET_PILLAR = {
    "h1_offense_xwoba": "offense",
    "h2_starter_xwoba_allowed": "starter",
    "h3_bullpen_xwoba_allowed": "bullpen",
    "h4_hard_hit_rolling_7d": "trend",
    "h4_hard_hit_rolling_14d": "trend",
}


def _attribute_event(event: dict, game: dict) -> dict | None:
    """Determina equipo que batea, equipo que lanza, y si el lanzador de
    ESE evento es el abridor registrado del partido -- `None` si el
    evento no se puede atribuir de forma confiable (inning_topbot
    faltante, o el partido no tiene abridor registrado)."""
    topbot = event.get("inning_topbot")
    if topbot not in ("Top", "Bot"):
        return None
    batting_team_id = game["away_team_id"] if topbot == "Top" else game["home_team_id"]
    pitching_team_id = game["home_team_id"] if topbot == "Top" else game["away_team_id"]
    starter_id = game["home_pitcher_id"] if pitching_team_id == game["home_team_id"] else game["away_pitcher_id"]
    is_starter = starter_id is not None and event.get("pitcher_id") == starter_id
    return {"batting_team_id": batting_team_id, "pitching_team_id": pitching_team_id, "is_starter": is_starter}


def compute_statcast_candidates(engine, seasons: list[int]) -> dict[tuple[int, int], dict]:
    """Un registro por juego (`season`, `game_pk`) con los 4 diffs H1-H4,
    calculados SOLO con eventos de fecha estrictamente anterior a la del
    juego (`event_date < game_date`), reseteado al inicio de cada
    temporada."""
    result: dict[tuple[int, int], dict] = {}

    for season in seasons:
        games_by_pk = {g["game_pk"]: g for g in historical_db.games_for_season(engine, season)}
        events = historical_db.statcast_events_for_seasons(engine, [season])

        events_by_game: dict[int, list[dict]] = {}
        for e in events:
            events_by_game.setdefault(e["game_pk"], []).append(e)

        by_date: dict = {}
        for game_pk, game in games_by_pk.items():
            if game.get("winner") is None:
                continue
            by_date.setdefault(game["game_date"], []).append(game)

        # Estado acumulado de la temporada, reseteado al entrar a cada temporada.
        offense_sum: dict[int, float] = {}
        offense_n: dict[int, int] = {}
        starter_allowed_sum: dict[int, float] = {}
        starter_allowed_n: dict[int, int] = {}
        bullpen_allowed_sum: dict[int, float] = {}
        bullpen_allowed_n: dict[int, int] = {}
        rolling_hard_hit: dict[int, list[tuple]] = {}  # team_id -> [(date, is_hard_hit), ...]

        def _offense_avg(team_id: int) -> float | None:
            n = offense_n.get(team_id, 0)
            return (offense_sum.get(team_id, 0.0) / n) if n > 0 else None

        def _starter_avg(pitcher_id: int | None) -> float | None:
            if pitcher_id is None:
                return None
            n = starter_allowed_n.get(pitcher_id, 0)
            return (starter_allowed_sum.get(pitcher_id, 0.0) / n) if n > 0 else None

        def _bullpen_avg(team_id: int) -> float | None:
            n = bullpen_allowed_n.get(team_id, 0)
            return (bullpen_allowed_sum.get(team_id, 0.0) / n) if n > 0 else None

        def _rolling_hard_hit_rate(team_id: int, as_of: object, window_days: int) -> float | None:
            history = rolling_hard_hit.get(team_id, [])
            cutoff = as_of.toordinal() - window_days if hasattr(as_of, "toordinal") else None
            recent = [hh for d, hh in history if cutoff is None or d.toordinal() > cutoff]
            return (sum(recent) / len(recent)) if recent else None

        for game_date in sorted(by_date):
            day_games = by_date[game_date]

            # --- Paso 1: LEER estado pre-dia para todos los juegos de HOY ---
            for game in day_games:
                home_id, away_id = game["home_team_id"], game["away_team_id"]
                home_off, away_off = _offense_avg(home_id), _offense_avg(away_id)
                h1 = (home_off - away_off) if (home_off is not None and away_off is not None) else None

                home_starter_allowed = _starter_avg(game.get("home_pitcher_id"))
                away_starter_allowed = _starter_avg(game.get("away_pitcher_id"))
                h2 = (away_starter_allowed - home_starter_allowed) if (home_starter_allowed is not None and away_starter_allowed is not None) else None

                home_bp, away_bp = _bullpen_avg(home_id), _bullpen_avg(away_id)
                h3 = (away_bp - home_bp) if (home_bp is not None and away_bp is not None) else None

                entry = {"h1_offense_xwoba": h1, "h2_starter_xwoba_allowed": h2, "h3_bullpen_xwoba_allowed": h3}
                for key, window_days in ROLLING_WINDOW_DAYS.items():
                    home_rate = _rolling_hard_hit_rate(home_id, game_date, window_days)
                    away_rate = _rolling_hard_hit_rate(away_id, game_date, window_days)
                    entry[key] = (home_rate - away_rate) if (home_rate is not None and away_rate is not None) else None
                result[(season, game["game_pk"])] = entry

            # --- Paso 2: ACTUALIZAR estado con los eventos de HOY (recien despues) ---
            for game in day_games:
                for event in events_by_game.get(game["game_pk"], []):
                    attribution = _attribute_event(event, game)
                    if attribution is None:
                        continue
                    xwoba = event.get("xwoba")
                    launch_speed = event.get("launch_speed")
                    batting_team_id = attribution["batting_team_id"]
                    pitching_team_id = attribution["pitching_team_id"]

                    if xwoba is not None:
                        offense_sum[batting_team_id] = offense_sum.get(batting_team_id, 0.0) + xwoba
                        offense_n[batting_team_id] = offense_n.get(batting_team_id, 0) + 1
                        if attribution["is_starter"]:
                            pitcher_id = event.get("pitcher_id")
                            if pitcher_id is not None:
                                starter_allowed_sum[pitcher_id] = starter_allowed_sum.get(pitcher_id, 0.0) + xwoba
                                starter_allowed_n[pitcher_id] = starter_allowed_n.get(pitcher_id, 0) + 1
                        else:
                            bullpen_allowed_sum[pitching_team_id] = bullpen_allowed_sum.get(pitching_team_id, 0.0) + xwoba
                            bullpen_allowed_n[pitching_team_id] = bullpen_allowed_n.get(pitching_team_id, 0) + 1

                    if launch_speed is not None:
                        rolling_hard_hit.setdefault(batting_team_id, []).append((game_date, 1.0 if launch_speed >= HARD_HIT_THRESHOLD_MPH else 0.0))

    return result


def load_records_with_statcast_candidates(engine, seasons: list[int]) -> list[dict]:
    records = load_game_pillar_data(engine, seasons)
    candidates = compute_statcast_candidates(engine, seasons)
    out = []
    for r in records:
        entry = candidates.get((r["season"], r["game_pk"]))
        if entry is None:
            continue
        r["statcast_candidate_diffs"] = entry
        out.append(r)
    return out


def _individual_stats(non_null_diffs: np.ndarray, y_non_null: np.ndarray, n_missing: int, coverage_pct: float) -> dict:
    if non_null_diffs.size > 1 and non_null_diffs.std() > 0:
        return {
            "n_missing": n_missing, "coverage_pct": coverage_pct,
            "auc": float(roc_auc_score(y_non_null, non_null_diffs)) if np.unique(non_null_diffs).size > 1 else None,
            "ks_statistic": float(ks_2samp(non_null_diffs[y_non_null == 1], non_null_diffs[y_non_null == 0]).statistic),
            "pearson_corr_with_outcome": float(pearsonr(non_null_diffs, y_non_null)[0]),
            "mutual_information": float(mutual_info_classif(non_null_diffs.reshape(-1, 1), y_non_null, random_state=0)[0]),
        }
    return {"n_missing": n_missing, "coverage_pct": coverage_pct, "auc": None, "ks_statistic": None, "pearson_corr_with_outcome": None, "mutual_information": None}


def evaluate_statcast_candidates(records: list[dict]) -> dict:
    y_all = np.array([r["home_win"] for r in records])

    baseline_pairs_by_season: dict[int, list[tuple[float, int]]] = {}
    for r in records:
        baseline_pairs_by_season.setdefault(r["season"], []).append((r["evidence_score_raw"], r["home_win"]))
    baseline_loso = calibration.loso_fit_and_score(baseline_pairs_by_season)

    out = {}
    for hypothesis in ALL_HYPOTHESES:
        target_pillar = _TARGET_PILLAR[hypothesis]
        diffs = [r["statcast_candidate_diffs"][hypothesis] for r in records]
        non_null_mask = np.array([d is not None for d in diffs])
        non_null_diffs = np.array([d for d in diffs if d is not None], dtype=float)
        n_missing = int((~non_null_mask).sum())
        coverage_pct = float(non_null_mask.mean())

        individual_stats = _individual_stats(non_null_diffs, y_all[non_null_mask], n_missing, coverage_pct)
        mean = float(non_null_diffs.mean()) if non_null_diffs.size else 0.0
        std = float(non_null_diffs.std()) if non_null_diffs.size else 0.0

        z = np.array([((d - mean) / std) if (d is not None and std > 0) else 0.0 for d in diffs])
        pairs_by_season: dict[int, list[tuple[float, int]]] = {}
        for r, zi in zip(records, z):
            score = sum(r["weights"][p] * (zi if p == target_pillar else r["advantages"][p]) for p in SEVEN_PILLARS)
            pairs_by_season.setdefault(r["season"], []).append((score, r["home_win"]))
        loso = calibration.loso_fit_and_score(pairs_by_season)
        ci = _paired_bootstrap_ci(baseline_loso["loso_pairs"], loso["loso_pairs"])

        out[hypothesis] = {
            "target_pillar": target_pillar,
            "individual_stats": individual_stats,
            "loso_if_substituted": {
                "loso_brier": loso["loso_brier"], "loso_log_loss": loso["loso_log_loss"],
                "loso_accuracy": loso["loso_accuracy"], "loso_ece": loso["loso_ece"],
                "per_season_metrics": loso["per_season_metrics"],
            },
            "current_target_pillar_per_season_metrics": baseline_loso["per_season_metrics"],
            "bootstrap_ci_delta_brier_vs_current": ci,
        }
    return out


def run_full_statcast_candidate_audit(seasons: list[int], historical_database_url: str) -> dict:
    engine = historical_db.get_engine(historical_database_url)
    historical_db.init_historical_storage(engine)
    records = load_records_with_statcast_candidates(engine, seasons)
    if not records:
        return {"n_games": 0, "seasons_used": seasons, "error": "no_games_with_full_data"}

    result = {
        "n_games": len(records),
        "seasons_used": seasons,
        "phase_loso_comparison": evaluate_statcast_candidates(records),
    }
    logger.info("run_full_statcast_candidate_audit completo -- n_games=%d", len(records))
    return result
