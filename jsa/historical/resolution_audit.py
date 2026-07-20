"""Segunda generacion de la auditoria de poder discriminativo -- solo la
parte que es honestamente respondible SIN nueva ingesta (nunca golpea la
API de MLB, nunca requiere una migracion de schema): sensibilidad de la
resolucion de discretizacion (-2..2 vs mas niveles vs percentiles vs
z-score vs continuo puro) para los 3 pilares que tienen un diff continuo
subyacente limpio (starter/bullpen/offense), y alternativas a
`team_quality` (Elo, Pythagorean Expectation) calculables 100% offline a
partir de `historical_game` ya ingerido (resultados juego a juego).

Explicitamente NO incluye Trend/Historical reales, ni BaseRuns/WAR para
team_quality -- esos requieren datos que este proyecto no tiene ingeridos
(wRC+/xFIP/WAR son de FanGraphs, nunca integrado aqui) o requieren nueva
ingesta contra la API de MLB (horas de GitHub Actions por temporada,
igual que el resto del historico). Ver ROADMAP.md para el detalle
completo de por que se dejaron afuera de esta entrega.

`team_quality`/`context`/`trend`/`historical` NUNCA se tocan en el sweep
de discretizacion -- solo starter/bullpen/offense cambian de
representacion; los otros 4 pilares mantienen su advantage discreto ya
persistido, exactamente como lo veria produccion hoy. Esto aisla el
efecto de la resolucion de esos 3 pilares sin confundirlo con cualquier
otro cambio."""

from __future__ import annotations

import logging

import numpy as np
from scipy.spatial.distance import jensenshannon
from scipy.stats import kurtosis, ks_2samp, pearsonr, skew
from sklearn.feature_selection import mutual_info_classif
from sklearn.metrics import roc_auc_score

from jsa.config import LEAGUE_AVG_ERA
from jsa.domain.models import SEVEN_PILLARS
from jsa.engine.pillars.base import discretize_diff, offense_factor, shrunk_era
from jsa.historical import calibration
from jsa.historical import db as historical_db
from jsa.historical.discriminative_audit import load_game_pillar_data
from jsa.historical.significance import paired_bootstrap_ci as _paired_bootstrap_ci

logger = logging.getLogger("jsa.historical")

_LEAGUE_OPS_FALLBACK = 0.750
_STARTER_UNIT = 0.55
_BULLPEN_UNIT = 0.45
_CLOSER_UNAVAILABLE_PENALTY = 0.30
_OFFENSE_UNIT = 0.06

DISCRETIZATION_CONFIGS = ("A_actual_-2_2", "B_-3_3", "C_-4_4", "D_percentiles_5", "E_zscore_continuo", "F_raw_continuo")


# --- Diffs continuos subyacentes (mismas formulas que produccion, ANTES de discretize_diff) ---


def _starter_diff(snapshot: dict) -> float | None:
    home_xera, away_xera = snapshot.get("home_starter_xera"), snapshot.get("away_starter_xera")
    if home_xera is None and away_xera is None:
        return None
    league_era = snapshot.get("league_avg_era") or LEAGUE_AVG_ERA
    home_era = shrunk_era(home_xera, snapshot.get("home_starter_ip_sample") or 0, league_era) if home_xera is not None else league_era
    away_era = shrunk_era(away_xera, snapshot.get("away_starter_ip_sample") or 0, league_era) if away_xera is not None else league_era
    return away_era - home_era


def _bullpen_diff(snapshot: dict) -> float | None:
    home_bp, away_bp = snapshot.get("home_bullpen_era"), snapshot.get("away_bullpen_era")
    if home_bp is None and away_bp is None:
        return None
    league_era = snapshot.get("league_avg_era") or LEAGUE_AVG_ERA
    home_e = shrunk_era(home_bp, snapshot.get("home_bullpen_ip_sample") or 0, league_era) if home_bp is not None else league_era
    away_e = shrunk_era(away_bp, snapshot.get("away_bullpen_ip_sample") or 0, league_era) if away_bp is not None else league_era
    home_eff = home_e + (_CLOSER_UNAVAILABLE_PENALTY if snapshot.get("home_closer_available") is False else 0.0)
    away_eff = away_e + (_CLOSER_UNAVAILABLE_PENALTY if snapshot.get("away_closer_available") is False else 0.0)
    return away_eff - home_eff


def _offense_diff(snapshot: dict) -> float | None:
    home_ops, away_ops = snapshot.get("home_ops"), snapshot.get("away_ops")
    if home_ops is None and away_ops is None:
        return None
    league_ops = snapshot.get("league_avg_ops") or _LEAGUE_OPS_FALLBACK
    home = home_ops if home_ops is not None else league_ops
    away = away_ops if away_ops is not None else league_ops
    return offense_factor(home, league_ops) - offense_factor(away, league_ops)


_DIFF_FNS = {"starter": _starter_diff, "bullpen": _bullpen_diff, "offense": _offense_diff}
_UNITS = {"starter": _STARTER_UNIT, "bullpen": _BULLPEN_UNIT, "offense": _OFFENSE_UNIT}


def load_records_with_raw_diffs(engine, seasons: list[int]) -> list[dict]:
    """Extiende `load_game_pillar_data()` con el diff continuo subyacente
    de starter/bullpen/offense (`None` si el juego no tenia datos
    suficientes para ese pilar -- en ese caso se usa el advantage discreto
    ya persistido como fallback, igual que produccion)."""
    records = load_game_pillar_data(engine, seasons)
    for r in records:
        snapshot = r["snapshot"]
        r["raw_diffs"] = {p: _DIFF_FNS[p](snapshot) for p in _DIFF_FNS}
    return records


def _quantile_thresholds(values: np.ndarray, n_bins: int = 5) -> np.ndarray:
    qs = np.linspace(0, 100, n_bins + 1)[1:-1]
    return np.percentile(values, qs)


def _config_value(diff: float, config: str, unit: float, quantile_edges: np.ndarray, mean: float, std: float) -> float:
    if config == "A_actual_-2_2":
        return float(max(-2, min(2, round(diff / unit))))
    if config == "B_-3_3":
        return float(max(-3, min(3, round(diff / unit))))
    if config == "C_-4_4":
        return float(max(-4, min(4, round(diff / unit))))
    if config == "D_percentiles_5":
        return float(np.searchsorted(quantile_edges, diff) - 2)  # centra en 0, rango -2..2
    if config == "E_zscore_continuo":
        return float((diff - mean) / std) if std > 0 else 0.0
    if config == "F_raw_continuo":
        return diff
    raise ValueError(f"config desconocida: {config}")


def _evidence_score_for_config(records: list[dict], config: str) -> list[tuple[int, float, int]]:
    """Devuelve (season, score, home_win) recalculando SOLO starter/bullpen/
    offense segun `config` -- team_quality/context/trend/historical quedan
    con su advantage discreto original, sin tocar."""
    replaced = list(_DIFF_FNS)
    diffs_by_pillar = {p: np.array([r["raw_diffs"][p] if r["raw_diffs"][p] is not None else 0.0 for r in records]) for p in replaced}
    stats_by_pillar = {}
    for p in replaced:
        d = diffs_by_pillar[p]
        stats_by_pillar[p] = {
            "quantile_edges": _quantile_thresholds(d, n_bins=5),
            "mean": float(d.mean()), "std": float(d.std()),
        }

    out = []
    for i, r in enumerate(records):
        score = 0.0
        for p in SEVEN_PILLARS:
            if p in replaced:
                if r["raw_diffs"][p] is None:
                    value = float(r["advantages"][p])  # sin datos -> usar el advantage ya persistido (igual que produccion)
                else:
                    s = stats_by_pillar[p]
                    value = _config_value(r["raw_diffs"][p], config, _UNITS[p], s["quantile_edges"], s["mean"], s["std"])
            else:
                value = float(r["advantages"][p])
            score += r["weights"][p] * value
        out.append((r["season"], score, r["home_win"]))
    return out


def _distribution_and_separability(scores: np.ndarray, y: np.ndarray) -> dict:
    won, lost = scores[y == 1], scores[y == 0]
    ks = ks_2samp(won, lost)
    pooled_var = ((len(won) - 1) * won.var(ddof=1) + (len(lost) - 1) * lost.var(ddof=1)) / (len(won) + len(lost) - 2)
    pooled_std = float(np.sqrt(pooled_var)) if pooled_var > 0 else 0.0
    cohens_d = float((won.mean() - lost.mean()) / pooled_std) if pooled_std > 0 else 0.0
    bins = np.linspace(scores.min(), scores.max(), 31) if scores.min() != scores.max() else np.linspace(-1, 1, 31)
    p_hist, _ = np.histogram(won, bins=bins)
    q_hist, _ = np.histogram(lost, bins=bins)
    p_dist = (p_hist + 1e-9) / (p_hist.sum() + 1e-9 * len(p_hist))
    q_dist = (q_hist + 1e-9) / (q_hist.sum() + 1e-9 * len(q_hist))
    return {
        "mean": float(scores.mean()), "std": float(scores.std()),
        "skewness": float(skew(scores)), "kurtosis": float(kurtosis(scores)),
        "pct_within_plus_minus_0_5_std": float(np.mean(np.abs(scores - scores.mean()) <= 0.5 * scores.std())) if scores.std() > 0 else None,
        "ks_statistic": float(ks.statistic), "ks_pvalue": float(ks.pvalue), "cohens_d": cohens_d,
        "jensen_shannon_divergence": float(jensenshannon(p_dist, q_dist) ** 2),
    }


def run_discretization_sweep(records: list[dict]) -> dict:
    """Fases 3 + 8: corre las 6 configuraciones sobre el MISMO pipeline
    (mismos pesos, misma calibracion LOSO), cambiando unicamente como se
    representa starter/bullpen/offense antes de sumarlos al Evidence
    Score. `A_actual_-2_2` es la configuracion de produccion -- referencia."""
    results = {}
    for config in DISCRETIZATION_CONFIGS:
        triples = _evidence_score_for_config(records, config)
        pairs_by_season: dict[int, list[tuple[float, int]]] = {}
        for season, score, y in triples:
            pairs_by_season.setdefault(season, []).append((score, y))
        loso = calibration.loso_fit_and_score(pairs_by_season)
        scores = np.array([s for _, s, _ in triples])
        y = np.array([yy for _, _, yy in triples])
        dist = _distribution_and_separability(scores, y)
        results[config] = {
            "loso_brier": loso["loso_brier"], "loso_log_loss": loso["loso_log_loss"],
            "loso_accuracy": loso["loso_accuracy"], "loso_ece": loso["loso_ece"], "loso_mce": loso["loso_mce"],
            "per_season_metrics": loso["per_season_metrics"],
            "loso_pairs": loso["loso_pairs"],
            **dist,
        }
    baseline_pairs = results["A_actual_-2_2"]["loso_pairs"]
    for config, entry in results.items():
        entry["bootstrap_ci_delta_brier_vs_actual"] = (
            _paired_bootstrap_ci(baseline_pairs, entry["loso_pairs"]) if config != "A_actual_-2_2" else None
        )
        del entry["loso_pairs"]  # no persistir predicciones crudas por juego en el JSON final
    return results


# --- Fase 7 parcial: alternativas a team_quality calculables offline (Elo, Pythagorean) ---


def compute_elo_and_pythagorean(engine, seasons: list[int], *, elo_k: float = 20.0, elo_start: float = 1500.0, pyth_exponent: float = 1.83) -> dict[tuple[int, int], dict]:
    """Elo y Pythagorean Expectation point-in-time-safe, calculados 100%
    offline sobre `historical_game` ya ingerido (nunca golpea la API).

    Elo se reinicia en `elo_start` al comienzo de CADA temporada (no
    carga rating de la temporada anterior) -- simplificacion deliberada
    para no inventar un factor de regresion entre temporadas sin evidencia
    propia; se documenta aqui, no se oculta.

    Ambos se actualizan por DIA calendario, no por juego individual: todos
    los juegos de una misma fecha usan el rating/RS-RA vigente al INICIO
    de esa fecha (nunca el de otro juego de la misma fecha) -- evita
    cualquier riesgo de fuga intra-dia cuando no hay hora exacta de inicio
    disponible (solo `game_date`, sin timestamp)."""
    result: dict[tuple[int, int], dict] = {}
    for season in seasons:
        games = historical_db.games_for_season(engine, season)
        finished = [g for g in games if g.get("winner") is not None]
        finished.sort(key=lambda g: (g["game_date"], g["game_pk"]))

        elo: dict[int, float] = {}
        runs_scored: dict[int, int] = {}
        runs_allowed: dict[int, int] = {}

        by_date: dict = {}
        for g in finished:
            by_date.setdefault(g["game_date"], []).append(g)

        for game_date in sorted(by_date):
            day_games = by_date[game_date]
            for g in day_games:
                h, a = g["home_team_id"], g["away_team_id"]
                h_elo = elo.get(h, elo_start)
                a_elo = elo.get(a, elo_start)
                h_rs, h_ra = runs_scored.get(h, 0), runs_allowed.get(h, 0)
                a_rs, a_ra = runs_scored.get(a, 0), runs_allowed.get(a, 0)
                h_pyth = (h_rs ** pyth_exponent) / (h_rs ** pyth_exponent + h_ra ** pyth_exponent) if (h_rs + h_ra) > 0 else 0.5
                a_pyth = (a_rs ** pyth_exponent) / (a_rs ** pyth_exponent + a_ra ** pyth_exponent) if (a_rs + a_ra) > 0 else 0.5
                result[(season, g["game_pk"])] = {
                    "elo_diff": h_elo - a_elo, "pythagorean_diff": h_pyth - a_pyth,
                    "home_elo": h_elo, "away_elo": a_elo, "home_pyth": h_pyth, "away_pyth": a_pyth,
                }
            for g in day_games:
                h, a = g["home_team_id"], g["away_team_id"]
                h_elo, a_elo = elo.get(h, elo_start), elo.get(a, elo_start)
                expected_home = 1.0 / (1.0 + 10 ** ((a_elo - h_elo) / 400.0))
                home_won = 1.0 if g["winner"] == "home" else 0.0
                elo[h] = h_elo + elo_k * (home_won - expected_home)
                elo[a] = a_elo + elo_k * ((1.0 - home_won) - (1.0 - expected_home))
                runs_scored[h] = runs_scored.get(h, 0) + g["home_score"]
                runs_allowed[h] = runs_allowed.get(h, 0) + g["away_score"]
                runs_scored[a] = runs_scored.get(a, 0) + g["away_score"]
                runs_allowed[a] = runs_allowed.get(a, 0) + g["home_score"]
    return result


def evaluate_team_quality_alternatives(records: list[dict], engine, seasons: list[int]) -> dict:
    """Mide (nunca reemplaza) Elo y Pythagorean Expectation como
    alternativas standalone a `team_quality`: estadisticas individuales
    (AUC/MI/KS/correlacion, igual que Fase 1) y el LOSO resultante de
    SUSTITUIR unicamente el valor de `team_quality` (z-scoreado, mismo
    peso) por cada alternativa, comparado via bootstrap contra dejar
    `team_quality` como esta hoy."""
    ratings = compute_elo_and_pythagorean(engine, seasons)
    y = np.array([r["home_win"] for r in records])
    baseline_pairs_by_season: dict[int, list[tuple[float, int]]] = {}
    for r in records:
        baseline_pairs_by_season.setdefault(r["season"], []).append((r["evidence_score_raw"], r["home_win"]))
    baseline_loso = calibration.loso_fit_and_score(baseline_pairs_by_season)

    out = {}
    for key, label in (("elo_diff", "elo"), ("pythagorean_diff", "pythagorean_expectation")):
        values = np.array([ratings.get((r["season"], r["game_pk"]), {}).get(key, 0.0) for r in records])
        individual_stats = {
            "auc": float(roc_auc_score(y, values)) if np.unique(values).size > 1 else None,
            "ks_statistic": float(ks_2samp(values[y == 1], values[y == 0]).statistic),
            "pearson_corr_with_outcome": float(pearsonr(values, y)[0]),
            "mutual_information": float(mutual_info_classif(values.reshape(-1, 1), y, random_state=0)[0]),
            "correlation_with_team_quality": float(pearsonr(values, [r["advantages"]["team_quality"] for r in records])[0]),
        }
        mean, std = values.mean(), values.std()
        z = (values - mean) / std if std > 0 else np.zeros_like(values)
        pairs_by_season: dict[int, list[tuple[float, int]]] = {}
        for r, zi in zip(records, z):
            score = sum(r["weights"][p] * (zi if p == "team_quality" else r["advantages"][p]) for p in SEVEN_PILLARS)
            pairs_by_season.setdefault(r["season"], []).append((score, r["home_win"]))
        loso = calibration.loso_fit_and_score(pairs_by_season)
        ci = _paired_bootstrap_ci(baseline_loso["loso_pairs"], loso["loso_pairs"])
        out[label] = {
            "individual_stats": individual_stats,
            "loso_if_substituted": {
                "loso_brier": loso["loso_brier"], "loso_log_loss": loso["loso_log_loss"],
                "loso_accuracy": loso["loso_accuracy"], "loso_ece": loso["loso_ece"],
                "per_season_metrics": loso["per_season_metrics"],
            },
            "current_team_quality_per_season_metrics": baseline_loso["per_season_metrics"],
            "bootstrap_ci_delta_brier_vs_actual_team_quality": ci,
        }
    return out


def run_full_resolution_audit(seasons: list[int], historical_database_url: str) -> dict:
    engine = historical_db.get_engine(historical_database_url)
    historical_db.init_historical_storage(engine)
    records = load_records_with_raw_diffs(engine, seasons)
    if not records:
        return {"n_games": 0, "seasons_used": seasons, "error": "no_games_with_full_data"}

    result = {
        "n_games": len(records),
        "seasons_used": seasons,
        "phase3_8_discretization_sweep": run_discretization_sweep(records),
        "phase7_team_quality_alternatives": evaluate_team_quality_alternatives(records, engine, seasons),
    }
    logger.info("run_full_resolution_audit completo -- n_games=%d", len(records))
    return result
