"""
Entrenamiento / recalibración -- prueba valores alternativos de parámetros
(por ahora, la dispersión k del modelo Binomial Negativo) contra el
histórico ya acumulado, y guarda una PROPUESTA en HistoricalSimulation.

NUNCA sobrescribe config.NEGBIN_DISPERSION ni ningún parámetro de
producción -- `HistoricalSimulation.applied` queda siempre False. Aplicar
un cambio de parámetro a producción es una decisión manual fuera de este
módulo (editar config.py a mano, con su propio commit y revisión).

Reutiliza model.negbin_model.negbin_win_prob() -- función PURA (sin
estado, sin DB), la misma que usa producción -- para que "qué hubiera
pasado con k=X" se calcule con la matemática EXACTA de producción, nunca
una reimplementación paralela que pudiera divergir.
"""

from config import NEGBIN_DISPERSION, PARK_FACTOR_WEIGHT, WEATHER_CORRECTION, HOME_FIELD_ADVANTAGE, STARTER_WEIGHT
from model.negbin_model import negbin_win_prob
from model.predictor import predict_from_raw_inputs
from model.runs_projection import HOME_FIELD_RUNS_BONUS
from model.skellam_model import skellam_win_prob

from historical_engine.db import HistoricalAnalysis, HistoricalGame, HistoricalSimulation, SessionLocal
from historical_engine.stats_utils import brier_score

# league_ops/league_era/league_avg_runs_per_game point-in-time SÍ se usan al
# ingerir (historical_engine/pipeline.py) pero no se persisten por fila en
# HistoricalAnalysis -- propose_starter_weight_recalibration() usa estos
# defaults de liga para TODOS los candidatos (baseline incluido), así que la
# comparación ENTRE candidatos es justa (mismo supuesto para todos), aunque
# el nivel absoluto de Brier no reproduce exactamente el de producción (que
# sí tuvo el valor real point-in-time). Ver docstring de la función.
_DEFAULT_LEAGUE_OPS = 0.750
_DEFAULT_LEAGUE_ERA = 4.30
_DEFAULT_LEAGUE_RUNS_PER_GAME = 4.4


def _actual_outcomes_by_game(run_id: int, session) -> dict:
    games = session.query(HistoricalGame).filter_by(run_id=run_id).all()
    return {g.game_pk: g.winner for g in games if g.winner is not None}


def _brier_for_dispersion(analyses: list, outcomes_by_game: dict, k: float) -> tuple[float | None, int]:
    probs, actuals = [], []
    for a in analyses:
        winner = outcomes_by_game.get(a.game_pk)
        if winner is None or a.home_proj_runs is None or a.away_proj_runs is None:
            continue
        home_prob = negbin_win_prob(a.home_proj_runs, a.away_proj_runs, k)
        probs.append(home_prob)
        actuals.append(1 if winner == "home" else 0)
    return brier_score(probs, actuals), len(probs)


def propose_dispersion_recalibration(
    season_year: int, run_id: int, candidate_values: list[float] | None = None,
    session_factory=None,
) -> dict:
    """
    Compara el Brier score de config.NEGBIN_DISPERSION (baseline) contra
    una lista de valores candidatos de k, usando el histórico ya
    reconstruido de `season_year`. Guarda la comparación completa en
    HistoricalSimulation (una fila por candidato) con `applied=False`
    siempre -- la fila con mejor Brier queda marcada `improved=True` sobre
    el baseline, nunca "aplicada".
    """
    candidate_values = candidate_values or [3.0, 5.0, 7.0, 9.0, 12.0, 20.0]
    session_factory = session_factory or SessionLocal
    session = session_factory()
    try:
        analyses = session.query(HistoricalAnalysis).filter_by(season_year=season_year).all()
        outcomes_by_game = _actual_outcomes_by_game(run_id, session)

        baseline_brier, baseline_n = _brier_for_dispersion(analyses, outcomes_by_game, NEGBIN_DISPERSION)

        proposals = []
        for k in candidate_values:
            candidate_brier, n = _brier_for_dispersion(analyses, outcomes_by_game, k)
            improved = (
                candidate_brier is not None and baseline_brier is not None and candidate_brier < baseline_brier
            )
            sim = HistoricalSimulation(
                run_id=run_id, season_year=season_year, param_name="NEGBIN_DISPERSION",
                baseline_value=NEGBIN_DISPERSION, proposed_value=k,
                based_on_metric="brier_score", baseline_metric_value=baseline_brier,
                proposed_metric_value=candidate_brier, improved=improved,
                notes=(
                    f"n={n} juegos con resultado real en {season_year}. Propuesta generada por "
                    f"historical_engine/training.py -- NO aplicada automáticamente, requiere edición "
                    f"manual de config.NEGBIN_DISPERSION y su propia revisión."
                ),
                applied=False,
            )
            session.add(sim)
            proposals.append({
                "param_value": k, "brier_score": candidate_brier, "n_sample": n, "improved_over_baseline": improved,
            })
        session.commit()
    finally:
        session.close()

    best = min(
        (p for p in proposals if p["brier_score"] is not None),
        key=lambda p: p["brier_score"], default=None,
    )

    return {
        "season_year": season_year,
        "baseline_value": NEGBIN_DISPERSION, "baseline_brier_score": baseline_brier, "baseline_n_sample": baseline_n,
        "proposals": proposals,
        "best_candidate": best,
        "applied": False,
        "note": "Ninguna propuesta se aplicó a producción -- config.NEGBIN_DISPERSION no fue modificado.",
    }


def _recompute_mu_with_candidate(
    a: HistoricalAnalysis, park_factor_weight: float, weather_correction: float,
) -> tuple[float | None, float | None]:
    """
    Deriva (home_mu, away_mu) bajo un `park_factor_weight`/`weather_correction`
    candidato, por inversión algebraica EXACTA de
    model/runs_projection.py::project_team_runs sobre home_proj_runs/
    away_proj_runs ya congelados en HistoricalAnalysis.

    Por qué invertir en vez de recalcular desde cero con project_team_runs():
    esa función necesita league_ops/league_era point-in-time, que SÍ se usan
    al ingerir (historical_engine/pipeline.py) pero NO se persisten por fila
    -- recalcular con el default de config.py en vez del valor real de esa
    fecha introduciría un error sistemático. La inversión evita el problema
    por completo: como la ingesta corrió con PARK_FACTOR_WEIGHT=1.0 y
    WEATHER_CORRECTION=0.0 (neutralizados, ver pipeline.py), hoy
    weighted_park_factor == park_factor y weather_impact == 0 EXACTOS, así
    que "base" (todo lo que no es park factor/clima/bono de local) se
    despeja sin aproximar nada, sin importar qué league_ops/era se usó.

    None si falta park_factor point-in-time para este juego (no se puede
    invertir sin él).
    """
    if not a.park_factor or a.home_proj_runs is None or a.away_proj_runs is None:
        return None, None

    weather_impact = weather_correction if (a.temp_f and a.temp_f > 85) else 0.0
    weighted_park_factor = 1.0 + (a.park_factor - 1.0) * park_factor_weight

    base_away = a.away_proj_runs / a.park_factor
    away_mu = max(base_away * weighted_park_factor * (1 + weather_impact), 0.3)

    base_home = (a.home_proj_runs - HOME_FIELD_RUNS_BONUS) / a.park_factor
    home_mu = max(base_home * weighted_park_factor * (1 + weather_impact) + HOME_FIELD_RUNS_BONUS, 0.3)

    return home_mu, away_mu


def propose_runs_projection_recalibration(
    season_year: int, run_id: int, param_name: str, candidate_values: list[float],
    session_factory=None,
) -> dict:
    """
    Compara el Brier score de Skellam con PARK_FACTOR_WEIGHT/WEATHER_CORRECTION
    vigentes (baseline -- ambos NEUTRALIZADOS hoy: 1.0 y 0.0 respectivamente,
    ver config.py) contra valores candidatos, recalculando home_mu/away_mu
    por juego vía _recompute_mu_with_candidate() sobre el histórico ya
    reconstruido de `season_year`. Reutiliza model.skellam_model.skellam_win_prob
    (la misma función de producción) para convertir mu a probabilidad --
    nunca una reimplementación paralela.

    Mismo criterio que propose_dispersion_recalibration: guarda una fila por
    candidato en HistoricalSimulation con `applied=False` siempre. NUNCA
    modifica config.py -- aplicar un cambio es una decisión manual aparte.
    """
    if param_name not in ("PARK_FACTOR_WEIGHT", "WEATHER_CORRECTION"):
        raise ValueError(f"param_name debe ser 'PARK_FACTOR_WEIGHT' o 'WEATHER_CORRECTION', no {param_name!r}")

    baseline_value = PARK_FACTOR_WEIGHT if param_name == "PARK_FACTOR_WEIGHT" else WEATHER_CORRECTION
    session_factory = session_factory or SessionLocal
    session = session_factory()
    try:
        analyses = session.query(HistoricalAnalysis).filter_by(season_year=season_year).all()
        outcomes_by_game = _actual_outcomes_by_game(run_id, session)

        def _brier_for(value):
            park_weight = value if param_name == "PARK_FACTOR_WEIGHT" else PARK_FACTOR_WEIGHT
            weather_corr = value if param_name == "WEATHER_CORRECTION" else WEATHER_CORRECTION
            probs, actuals = [], []
            for a in analyses:
                winner = outcomes_by_game.get(a.game_pk)
                if winner is None:
                    continue
                home_mu, away_mu = _recompute_mu_with_candidate(a, park_weight, weather_corr)
                if home_mu is None:
                    continue
                probs.append(skellam_win_prob(home_mu, away_mu))
                actuals.append(1 if winner == "home" else 0)
            return brier_score(probs, actuals), len(probs)

        baseline_brier, baseline_n = _brier_for(baseline_value)

        proposals = []
        for value in candidate_values:
            candidate_brier, n = _brier_for(value)
            improved = (
                candidate_brier is not None and baseline_brier is not None and candidate_brier < baseline_brier
            )
            sim = HistoricalSimulation(
                run_id=run_id, season_year=season_year, param_name=param_name,
                baseline_value=baseline_value, proposed_value=value,
                based_on_metric="brier_score", baseline_metric_value=baseline_brier,
                proposed_metric_value=candidate_brier, improved=improved,
                notes=(
                    f"n={n} juegos con resultado real en {season_year}. Skellam recalculado con "
                    f"_recompute_mu_with_candidate() (inversión algebraica exacta de "
                    f"project_team_runs) -- propuesta generada por historical_engine/training.py, "
                    f"NO aplicada automáticamente, requiere edición manual de config.py y su "
                    f"propia revisión."
                ),
                applied=False,
            )
            session.add(sim)
            proposals.append({
                "param_value": value, "brier_score": candidate_brier, "n_sample": n, "improved_over_baseline": improved,
            })
        session.commit()
    finally:
        session.close()

    best = min(
        (p for p in proposals if p["brier_score"] is not None),
        key=lambda p: p["brier_score"], default=None,
    )

    return {
        "season_year": season_year, "param_name": param_name,
        "baseline_value": baseline_value, "baseline_brier_score": baseline_brier, "baseline_n_sample": baseline_n,
        "proposals": proposals,
        "best_candidate": best,
        "applied": False,
        "note": f"Ninguna propuesta se aplicó a producción -- config.{param_name} no fue modificado.",
    }


def _raw_inputs_for_starter_weight_candidate(a: HistoricalAnalysis, starter_weight: float) -> dict | None:
    """
    Arma el dict `raw` que espera model.predictor.py::predict_from_raw_inputs
    a partir de una fila de HistoricalAnalysis, con `starter_weight` como
    único parámetro variable -- todo lo demás (ERA/OPS/bullpen/park_factor/
    temp_f, point-in-time) viene tal cual se congeló en la ingesta.

    league_ops/league_era/league_avg_runs_per_game usan el default de
    config.py (no el valor real point-in-time de ese juego, que no se
    persiste) -- ver nota al inicio del módulo. None si falta algún insumo
    obligatorio para predict_from_raw_inputs (era/ops/bullpen_era/park_factor).
    """
    required = (a.away_era, a.home_era, a.away_ops, a.home_ops, a.away_bullpen_era, a.home_bullpen_era, a.park_factor)
    if any(v is None for v in required):
        return None

    return {
        "away_era": a.away_era, "home_era": a.home_era,
        "away_ops": a.away_ops, "home_ops": a.home_ops,
        "away_bullpen_era": a.away_bullpen_era, "home_bullpen_era": a.home_bullpen_era,
        "away_innings_pitched": a.away_innings_pitched, "home_innings_pitched": a.home_innings_pitched,
        "away_k_pct": a.away_k_pct, "home_k_pct": a.home_k_pct,
        "away_bb_pct": a.away_bb_pct, "home_bb_pct": a.home_bb_pct,
        "away_days_rest": a.away_days_rest, "home_days_rest": a.home_days_rest,
        "park_factor": a.park_factor, "temp_f": a.temp_f,
        "starter_weight": starter_weight,
        "league_ops": _DEFAULT_LEAGUE_OPS, "league_era": _DEFAULT_LEAGUE_ERA,
        "league_avg_runs_per_game": _DEFAULT_LEAGUE_RUNS_PER_GAME,
        "park_factor_weight": PARK_FACTOR_WEIGHT, "weather_correction": WEATHER_CORRECTION,
        "home_field_advantage": HOME_FIELD_ADVANTAGE, "negbin_dispersion": NEGBIN_DISPERSION,
    }


def propose_starter_weight_recalibration(
    season_year: int, run_id: int, candidate_values: list[float] | None = None,
    session_factory=None,
) -> dict:
    """
    Compara el Brier score de Skellam con config.STARTER_WEIGHT vigente
    (0.65, nunca calibrado contra datos reales -- ver config.py) contra
    valores candidatos, llamando DIRECTAMENTE a
    model.predictor.py::predict_from_raw_inputs (la función real de
    producción, no una reimplementación) con cada candidato.

    A diferencia de propose_dispersion_recalibration/
    propose_runs_projection_recalibration, aquí SÍ se recalcula mu desde
    cero en vez de partir de home_proj_runs/away_proj_runs congelados,
    porque STARTER_WEIGHT determina cómo se pondera ERA de abridor vs.
    bullpen ANTES de multiplicar por el resto de factores -- no se puede
    despejar por álgebra simple como con el peso de parque. Usa
    league_ops/league_era/league_avg_runs_per_game default de config.py
    para TODOS los candidatos (ver nota al inicio del módulo) -- el Brier
    absoluto de este barrido no es comparable 1:1 con el de
    validate_source()/compare_models(), pero la comparación ENTRE
    candidatos (qué tan sensible es Brier a STARTER_WEIGHT) sí es válida,
    porque todos comparten el mismo supuesto de liga.

    Mismo criterio de siempre: guarda una fila por candidato en
    HistoricalSimulation con `applied=False`. NUNCA modifica config.py.
    """
    candidate_values = candidate_values or [0.3, 0.4, 0.5, 0.6, 0.65, 0.7, 0.8, 0.9]
    session_factory = session_factory or SessionLocal
    session = session_factory()
    try:
        analyses = session.query(HistoricalAnalysis).filter_by(season_year=season_year).all()
        outcomes_by_game = _actual_outcomes_by_game(run_id, session)

        def _brier_for(weight):
            probs, actuals = [], []
            for a in analyses:
                winner = outcomes_by_game.get(a.game_pk)
                if winner is None:
                    continue
                raw = _raw_inputs_for_starter_weight_candidate(a, weight)
                if raw is None:
                    continue
                prediction = predict_from_raw_inputs(raw)
                probs.append(prediction["home_skellam_prob"])
                actuals.append(1 if winner == "home" else 0)
            return brier_score(probs, actuals), len(probs)

        baseline_brier, baseline_n = _brier_for(STARTER_WEIGHT)

        proposals = []
        for weight in candidate_values:
            candidate_brier, n = _brier_for(weight)
            improved = (
                candidate_brier is not None and baseline_brier is not None and candidate_brier < baseline_brier
            )
            sim = HistoricalSimulation(
                run_id=run_id, season_year=season_year, param_name="STARTER_WEIGHT",
                baseline_value=STARTER_WEIGHT, proposed_value=weight,
                based_on_metric="brier_score", baseline_metric_value=baseline_brier,
                proposed_metric_value=candidate_brier, improved=improved,
                notes=(
                    f"n={n} juegos con resultado real en {season_year}. league_ops/league_era "
                    f"default de config.py (no point-in-time real, ver nota en training.py) -- "
                    f"Brier absoluto no comparable con validate_source(), solo la comparación "
                    f"ENTRE candidatos de este barrido. Propuesta generada por "
                    f"historical_engine/training.py, NO aplicada automáticamente, requiere "
                    f"edición manual de config.STARTER_WEIGHT y su propia revisión."
                ),
                applied=False,
            )
            session.add(sim)
            proposals.append({
                "param_value": weight, "brier_score": candidate_brier, "n_sample": n, "improved_over_baseline": improved,
            })
        session.commit()
    finally:
        session.close()

    best = min(
        (p for p in proposals if p["brier_score"] is not None),
        key=lambda p: p["brier_score"], default=None,
    )

    return {
        "season_year": season_year,
        "baseline_value": STARTER_WEIGHT, "baseline_brier_score": baseline_brier, "baseline_n_sample": baseline_n,
        "proposals": proposals,
        "best_candidate": best,
        "applied": False,
        "note": "Ninguna propuesta se aplicó a producción -- config.STARTER_WEIGHT no fue modificado.",
    }
