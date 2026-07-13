"""Validacion sobre historial real -- Brier, LogLoss, ECE/MCE, Home Bias
Audit (Seccion 13.3), y benchmarking obligatorio contra baselines
ingenuos y los modelos legado (Seccion 12.3 del spec JSA v3.0). Solo
tiene sentido una vez que `pipeline.py` ya ingirio una temporada -- antes
de eso no hay nada que validar (por eso este modulo no formaba parte de
la entrega anterior).

Nunca declara una mejora "significativa" (eso es Seccion 12.8 -- bootstrap/
McNemar/permutation, explicitamente fuera de esta entrega, ver
jsa/docs/ROADMAP.md): solo reporta los numeros crudos de cada modelo/
baseline, para que una iteracion siguiente les aplique la prueba formal."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from jsa.historical import db as historical_db
from jsa.legacy import heuristic_model
from jsa.legacy.calibration_constants import (
    HOME_FIELD_ADVANTAGE,
    LEAGUE_AVG_ERA_FALLBACK,
    LEAGUE_AVG_OPS_FALLBACK,
    NEGBIN_DISPERSION,
    STARTER_WEIGHT,
    calibrated_skellam_win_prob,
)
from jsa.legacy.negbin_model import negbin_win_prob
from jsa.engine.projected_runs import compute_projected_runs
from jsa.domain.models import GameSnapshot


@dataclass
class MetricSummary:
    n: int
    brier: float | None
    log_loss: float | None
    accuracy: float | None
    ece: float | None
    mce: float | None


def brier_score(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def log_loss(pairs: list[tuple[float, int]], eps: float = 1e-12) -> float | None:
    if not pairs:
        return None
    total = 0.0
    for p, y in pairs:
        p = min(max(p, eps), 1 - eps)
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(pairs)


def accuracy(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    correct = sum(1 for p, y in pairs if (p >= 0.5) == bool(y))
    return correct / len(pairs)


def _calibration_buckets(pairs: list[tuple[float, int]], n_bins: int = 10) -> list[dict]:
    buckets: list[dict] = [{"lo": i / n_bins, "hi": (i + 1) / n_bins, "preds": [], "actuals": []} for i in range(n_bins)]
    for p, y in pairs:
        idx = min(int(p * n_bins), n_bins - 1)
        buckets[idx]["preds"].append(p)
        buckets[idx]["actuals"].append(y)
    return buckets


def ece(pairs: list[tuple[float, int]], n_bins: int = 10) -> float | None:
    """Expected Calibration Error: promedio ponderado por tamaño de bucket
    de |confianza promedio - frecuencia real|."""
    if not pairs:
        return None
    buckets = _calibration_buckets(pairs, n_bins)
    total = len(pairs)
    error = 0.0
    for b in buckets:
        if not b["preds"]:
            continue
        conf = sum(b["preds"]) / len(b["preds"])
        freq = sum(b["actuals"]) / len(b["actuals"])
        error += (len(b["preds"]) / total) * abs(conf - freq)
    return error


def mce(pairs: list[tuple[float, int]], n_bins: int = 10) -> float | None:
    """Maximum Calibration Error: el peor bucket, no el promedio."""
    if not pairs:
        return None
    buckets = _calibration_buckets(pairs, n_bins)
    worst = 0.0
    for b in buckets:
        if not b["preds"]:
            continue
        conf = sum(b["preds"]) / len(b["preds"])
        freq = sum(b["actuals"]) / len(b["actuals"])
        worst = max(worst, abs(conf - freq))
    return worst


def summarize(pairs: list[tuple[float, int]]) -> MetricSummary:
    return MetricSummary(n=len(pairs), brier=brier_score(pairs), log_loss=log_loss(pairs), accuracy=accuracy(pairs), ece=ece(pairs), mce=mce(pairs))


@dataclass
class HomeBiasAuditResult:
    n: int
    pct_favoring_home: float | None
    actual_home_win_rate: float | None
    excess_home_favoritism_pp: float | None
    accuracy_when_favoring_home: float | None
    accuracy_when_favoring_away: float | None
    rejected: bool = field(default=False)
    rejection_reason: str | None = None


def home_bias_audit(pairs: list[tuple[float, int]]) -> HomeBiasAuditResult:
    """Seccion 13.3. Criterio de rechazo: exceso de favoritismo local >3pp,
    o accuracy en picks de visitante indistinguible de moneda mientras el
    de local no lo es (chequeo simplificado: sin IC de Wilson formal en
    esta entrega, ver Seccion 12.8/ROADMAP)."""
    if not pairs:
        return HomeBiasAuditResult(n=0, pct_favoring_home=None, actual_home_win_rate=None, excess_home_favoritism_pp=None, accuracy_when_favoring_home=None, accuracy_when_favoring_away=None)

    favors_home = [(p, y) for p, y in pairs if p >= 0.5]
    favors_away = [(p, y) for p, y in pairs if p < 0.5]

    pct_favoring_home = len(favors_home) / len(pairs)
    actual_home_win_rate = sum(y for _, y in pairs) / len(pairs)
    excess_pp = (pct_favoring_home - actual_home_win_rate) * 100

    acc_home = accuracy(favors_home) if favors_home else None
    acc_away = (1 - accuracy(favors_away)) if favors_away and accuracy(favors_away) is not None else None
    # accuracy() usa (p>=0.5)==y; para picks de visitante lo natural es medir
    # "acerto el visitante", que es (p<0.5) == (y==0) -- equivalente a
    # 1 - accuracy() sobre esos mismos pares tal como estan (y=1 es home-win).

    rejected = abs(excess_pp) > 3.0
    reason = f"exceso de favoritismo local {excess_pp:+.1f}pp (> 3pp)" if rejected else None

    return HomeBiasAuditResult(
        n=len(pairs), pct_favoring_home=pct_favoring_home, actual_home_win_rate=actual_home_win_rate,
        excess_home_favoritism_pp=excess_pp, accuracy_when_favoring_home=acc_home, accuracy_when_favoring_away=acc_away,
        rejected=rejected, rejection_reason=reason,
    )


def _legacy_predictions(snapshot: GameSnapshot) -> dict[str, float | None]:
    """Corre los 3 modelos legado (Seccion 12.3) sobre el MISMO snapshot
    reconstruido que uso el Evidence Engine -- nunca vuelve a golpear la API."""
    league_era = snapshot.league_avg_era or LEAGUE_AVG_ERA_FALLBACK
    league_ops = snapshot.league_avg_ops or LEAGUE_AVG_OPS_FALLBACK

    heuristic_prob = None
    if snapshot.home_starter_xera is not None and snapshot.away_starter_xera is not None and snapshot.home_ops is not None and snapshot.away_ops is not None:
        home_raw = heuristic_model.model_prob(
            p_era=snapshot.home_starter_xera, team_ops=snapshot.home_ops, league_ops=league_ops,
            bullpen_era=snapshot.home_bullpen_era, starter_weight=STARTER_WEIGHT,
        )
        away_raw = heuristic_model.model_prob(
            p_era=snapshot.away_starter_xera, team_ops=snapshot.away_ops, league_ops=league_ops,
            bullpen_era=snapshot.away_bullpen_era, starter_weight=STARTER_WEIGHT,
        )
        _, heuristic_prob = heuristic_model.normalize_matchup(away_raw, home_raw, home_field_adv=HOME_FIELD_ADVANTAGE)

    projected = compute_projected_runs(snapshot)
    skellam_calibrated = calibrated_skellam_win_prob(projected.mu_home, projected.mu_away)
    negbin_prob = negbin_win_prob(projected.mu_home, projected.mu_away, k=NEGBIN_DISPERSION)

    return {"legacy_heuristic": heuristic_prob, "legacy_skellam_calibrated": skellam_calibrated, "legacy_negbin": negbin_prob}


def _naive_baselines(snapshot: GameSnapshot, empirical_home_rate: float) -> dict[str, float]:
    """Seccion 12.3: predictor constante, "siempre favorece al local",
    "favorece al de mejor OPS", "favorece al de mejor ERA de abridor" --
    expresados como probabilidad de que gane el LOCAL, para poder
    puntuarlos con Brier/LogLoss igual que los demas."""
    result = {"naive_constant": empirical_home_rate, "naive_always_home": 0.99}

    if snapshot.home_ops is not None and snapshot.away_ops is not None:
        result["naive_best_ops"] = 0.7 if snapshot.home_ops >= snapshot.away_ops else 0.3
    if snapshot.home_starter_xera is not None and snapshot.away_starter_xera is not None:
        # ERA mas bajo es mejor.
        result["naive_best_starter_era"] = 0.7 if snapshot.home_starter_xera <= snapshot.away_starter_xera else 0.3

    return result


def benchmark_season(season: int, historical_database_url: str) -> dict:
    """Punto de entrada principal: lee todo lo ya ingerido para `season`
    (`pipeline.run_season_ingestion` debe haber corrido antes) y devuelve
    metricas de JSA + baselines ingenuos + modelos legado, lado a lado."""
    engine = historical_db.get_engine(historical_database_url)
    historical_db.init_historical_storage(engine)  # tolera una base historica nueva/vacia
    games = {g["game_pk"]: g for g in historical_db.games_for_season(engine, season)}
    snapshots = {s["game_pk"]: s for s in historical_db.snapshots_for_season(engine, season)}
    reports = historical_db.reports_for_season(engine, season)

    if not games:
        return {"season": season, "n": 0, "error": "sin juegos ingeridos -- correr jsa.historical.pipeline primero"}

    empirical_home_rate = sum(1 for g in games.values() if g.get("winner") == "home") / len(games)

    jsa_pairs: list[tuple[float, int]] = []
    legacy_pairs: dict[str, list[tuple[float, int]]] = {"legacy_heuristic": [], "legacy_skellam_calibrated": [], "legacy_negbin": []}
    naive_pairs: dict[str, list[tuple[float, int]]] = {"naive_constant": [], "naive_always_home": [], "naive_best_ops": [], "naive_best_starter_era": []}

    for report_row in reports:
        game_pk = report_row["game_pk"]
        game = games.get(game_pk)
        snap_row = snapshots.get(game_pk)
        if game is None or game.get("winner") is None or snap_row is None:
            continue
        actual_home_win = 1 if game["winner"] == "home" else 0

        report_payload = report_row["payload"]
        raw_prob = report_payload.get("calibration", {}).get("raw_probability")
        if raw_prob is not None:
            jsa_pairs.append((raw_prob, actual_home_win))

        snapshot = GameSnapshot(**snap_row["payload"])
        legacy = _legacy_predictions(snapshot)
        for name, prob in legacy.items():
            if prob is not None:
                legacy_pairs[name].append((prob, actual_home_win))

        naive = _naive_baselines(snapshot, empirical_home_rate)
        for name, prob in naive.items():
            naive_pairs[name].append((prob, actual_home_win))

    result = {
        "season": season,
        "n_games_ingested": len(games),
        "n_games_scored": len(jsa_pairs),
        "empirical_home_win_rate": empirical_home_rate,
        "jsa_evidence_engine": summarize(jsa_pairs).__dict__,
        "home_bias_audit": home_bias_audit(jsa_pairs).__dict__,
    }
    for name, pairs in {**legacy_pairs, **naive_pairs}.items():
        result[name] = summarize(pairs).__dict__

    return result
