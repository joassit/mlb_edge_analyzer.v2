"""Game Flow candidate audit -- Etapa 1 del Game Flow Engine v1.0 (ver
`jsa/docs/game_flow_design.md`). Evalua 2 hipotesis derivadas ENTERAMENTE
de campos ya persistidos en `historical_snapshot` (`home/away_starter_
projected_ip`, `home/away_starter_xera`, `home/away_starter_ip_sample`,
`home/away_bullpen_era`, `home/away_bullpen_ip_sample`, `home/away_closer_
available`) -- sin ninguna ingesta nueva, mismo protocolo LOSO + bootstrap
CI + criterio de 3 condiciones que Trend/Historical/Statcast/Elo-
Pythagorean (`jsa/docs/statcast_integration_design.md` Seccion 7).

- GF1 (`gf1_starter_durability`): sustituye el pilar `starter` (hoy
  ERA-diff con shrinkage) por un diff de "probabilidad de completar >=6
  entradas" (quality start), derivado de `projected_ip` (IP promedio por
  salida de temporada, el MISMO proxy que ya usa `context_detector.py`
  para `long_outing`/`short_outing`) modelado como
  Normal(mu=projected_ip, sigma=`_STARTER_IP_SIGMA_HEURISTIC`). Prueba una
  hipotesis DISTINTA de la que ya evalua `starter.py`: ¿la durabilidad
  esperada del abridor predice el resultado mejor/distinto que su ERA?
  Nota de honestidad: `sigma` es un heuristico de partida (no existe en
  este proyecto ningun dato real de IP efectivamente lanzada POR JUEGO
  para calibrar la dispersion de esta distribucion -- `historical_game`
  solo persiste el resultado final, ver auditoria de Game Flow) -- la
  MEDIA (`projected_ip`) si es un promedio real de temporada, point-in-
  time-safe, ya usado en produccion.

- GF2 (`gf2_bullpen_dependency`): sustituye el pilar `bullpen` (hoy
  ERA-diff con shrinkage + penalizacion de closer) por ese mismo diff,
  escalado por cuanto se espera que dependa el partido del bullpen
  (`expected_bullpen_ip` de cada equipo = 9 - `projected_ip` de su propio
  abridor). Prueba la hipotesis: la ventaja de bullpen importa mas en
  partidos donde ambos equipos van a depender mas de el.

Deliberadamente FUERA de este modulo (componente 3 de la propuesta del
usuario, "Closer Rating"): separar el ERA del cerrador del resto del
bullpen requiere un campo NUEVO en `historical_snapshot` --
`point_in_time_provider.py::bullpen_era_as_of()` hoy calcula el ERA
individual de cada relevista para identificar al cerrador pero lo
descarta despues de la agregacion (nunca lo persiste por separado). Igual
que cualquier campo nuevo anterior (Trend, fielding%), esto exige su
propia re-ingesta de las 5 temporadas y su propia autorizacion explicita
antes de correrla -- no se construye en esta entrega."""

from __future__ import annotations

import numpy as np
from scipy.stats import ks_2samp, norm, pearsonr
from sklearn.feature_selection import mutual_info_classif
from sklearn.metrics import roc_auc_score

from jsa.domain.models import SEVEN_PILLARS
from jsa.engine.pillars.base import shrunk_era
from jsa.historical import calibration
from jsa.historical import db as historical_db
from jsa.historical.discriminative_audit import _paired_bootstrap_ci, load_game_pillar_data

_STARTER_IP_SIGMA_HEURISTIC = 1.2
_QUALITY_START_IP = 6.0
_CLOSER_UNAVAILABLE_PENALTY = 0.30
_LEAGUE_AVG_ERA_FALLBACK = 4.30
_GAME_INNINGS = 9.0
_MIN_EXPECTED_BULLPEN_IP = 1.0

ALL_HYPOTHESES = ("gf1_starter_durability", "gf2_bullpen_dependency")
_TARGET_PILLAR = {"gf1_starter_durability": "starter", "gf2_bullpen_dependency": "bullpen"}


def _prob_ip_ge(projected_ip: float | None, threshold: float = _QUALITY_START_IP, sigma: float = _STARTER_IP_SIGMA_HEURISTIC) -> float | None:
    if projected_ip is None:
        return None
    return float(1.0 - norm.cdf((threshold - projected_ip) / sigma))


def _expected_bullpen_ip(projected_ip: float | None) -> float:
    if projected_ip is None:
        return _GAME_INNINGS / 2.0  # sin dato -> asumir reparto neutral, mismo criterio que discretize_diff sin datos
    return max(_MIN_EXPECTED_BULLPEN_IP, _GAME_INNINGS - projected_ip)


def _gf1_starter_durability_diff(snapshot: dict) -> float | None:
    home_p = _prob_ip_ge(snapshot.get("home_starter_projected_ip"))
    away_p = _prob_ip_ge(snapshot.get("away_starter_projected_ip"))
    if home_p is None and away_p is None:
        return None
    return (home_p if home_p is not None else 0.5) - (away_p if away_p is not None else 0.5)


def _gf2_bullpen_dependency_diff(snapshot: dict, league_avg_era: float) -> float | None:
    home_bp, away_bp = snapshot.get("home_bullpen_era"), snapshot.get("away_bullpen_era")
    if home_bp is None and away_bp is None:
        return None
    home_era = (
        shrunk_era(home_bp, snapshot.get("home_bullpen_ip_sample") or 0, league_avg_era) if home_bp is not None else league_avg_era
    )
    away_era = (
        shrunk_era(away_bp, snapshot.get("away_bullpen_ip_sample") or 0, league_avg_era) if away_bp is not None else league_avg_era
    )
    home_effective = home_era + (_CLOSER_UNAVAILABLE_PENALTY if snapshot.get("home_closer_available") is False else 0.0)
    away_effective = away_era + (_CLOSER_UNAVAILABLE_PENALTY if snapshot.get("away_closer_available") is False else 0.0)
    era_diff = away_effective - home_effective  # positivo favorece a home, mismo signo que bullpen.py

    home_expected_bp_ip = _expected_bullpen_ip(snapshot.get("home_starter_projected_ip"))
    away_expected_bp_ip = _expected_bullpen_ip(snapshot.get("away_starter_projected_ip"))
    dependency_factor = ((home_expected_bp_ip + away_expected_bp_ip) / 2.0) / _GAME_INNINGS
    return era_diff * dependency_factor


def load_records_with_game_flow_candidates(engine, seasons: list[int]) -> list[dict]:
    """Extiende `load_game_pillar_data()` (los mismos registros que ya usan
    `resolution_audit.py`/`statcast_candidate_audit.py`) con los diffs
    continuos de GF1/GF2 -- ninguno de los dos requiere tablas nuevas,
    solo campos ya presentes en el payload de `historical_snapshot`."""
    records = load_game_pillar_data(engine, seasons)
    for r in records:
        snapshot = r["snapshot"]
        league_era = snapshot.get("league_avg_era") or _LEAGUE_AVG_ERA_FALLBACK
        r["game_flow_diffs"] = {
            "gf1_starter_durability": _gf1_starter_durability_diff(snapshot),
            "gf2_bullpen_dependency": _gf2_bullpen_dependency_diff(snapshot, league_era),
        }
    return records


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


def evaluate_game_flow_candidates(records: list[dict]) -> dict:
    """Sustituye UNICAMENTE el pilar objetivo (z-scoreado, mismo peso que
    produccion) y compara via LOSO + bootstrap CI contra el valor REAL de
    produccion de ese pilar -- nunca contra el pilar en 0, porque
    starter/bullpen ya son "imprescindibles" (ver diagnostico del techo
    del modelo, ROADMAP.md)."""
    y_all = np.array([r["home_win"] for r in records])

    baseline_pairs_by_season: dict[int, list[tuple[float, int]]] = {}
    for r in records:
        baseline_pairs_by_season.setdefault(r["season"], []).append((r["evidence_score_raw"], r["home_win"]))
    baseline_loso = calibration.loso_fit_and_score(baseline_pairs_by_season)

    out = {}
    for hypothesis in ALL_HYPOTHESES:
        target_pillar = _TARGET_PILLAR[hypothesis]
        diffs = [r["game_flow_diffs"][hypothesis] for r in records]
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


def run_full_game_flow_candidate_audit(seasons: list[int], historical_database_url: str) -> dict:
    engine = historical_db.get_engine(historical_database_url)
    historical_db.init_historical_storage(engine)
    records = load_records_with_game_flow_candidates(engine, seasons)
    if not records:
        return {"n_games": 0, "seasons_used": seasons, "error": "no_games_with_full_data"}
    return {
        "n_games": len(records),
        "seasons_used": seasons,
        "phase_loso_comparison": evaluate_game_flow_candidates(records),
    }
