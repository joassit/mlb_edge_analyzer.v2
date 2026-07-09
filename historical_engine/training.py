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

from config import NEGBIN_DISPERSION
from model.negbin_model import negbin_win_prob

from historical_engine.db import HistoricalAnalysis, HistoricalGame, HistoricalSimulation, SessionLocal
from historical_engine.stats_utils import brier_score


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
