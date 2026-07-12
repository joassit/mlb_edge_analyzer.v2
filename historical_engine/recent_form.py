"""
Forma reciente: recalcula OPS de equipo / ERA de abridor / ERA de bullpen
sobre una VENTANA MÓVIL de los últimos N días (en vez del acumulado de
temporada completa que usa todo el sistema hoy) a partir de los logs
crudos cacheados por raw_ingestion.py -- aritmética 100% local, cero
llamadas a la API.

Motivación (hipótesis de la brecha con el mercado): el modelo promedia la
temporada ENTERA hasta la fecha de corte -- en agosto, 3 malas salidas
recientes de un abridor pesan casi nada contra 25 acumuladas, mientras el
mercado ajusta el precio en días. Si la forma reciente tiene señal real,
mezclarla con el acumulado debería mejorar el Brier del histórico.

evaluate_recent_form() corre el experimento contra una temporada ya
ingerida: reconstruye la probabilidad Skellam de cada juego con insumos
mezclados (peso w para la ventana, 1-w para el acumulado point-in-time ya
congelado en HistoricalAnalysis) y compara Brier contra el baseline
(w=0.0, solo acumulado). Mismo contrato que training.py: persiste
propuestas en HistoricalSimulation con applied=False, NUNCA modifica
producción.

Umbrales de muestra mínima: una ventana de 15 días puede contener 2-3
salidas de un abridor (ERA ruidosísimo) o un puñado de juegos de bullpen
-- por debajo del mínimo, esa variable cae al valor de temporada accumulada
(equivale a w=0 SOLO para esa variable de ese juego, nunca se inventa).
"""

import logging
from collections import defaultdict
from datetime import date, timedelta

from config import STARTER_WEIGHT

from model.predictor import predict_from_raw_inputs

from historical_engine.db import (
    HistoricalAnalysis, HistoricalGame, HistoricalRawBattingLog,
    HistoricalRawPitchingLog, HistoricalRawRosterSnapshot,
    HistoricalSimulation, SessionLocal,
)
from historical_engine.stats_utils import brier_score
from historical_engine.training import _raw_inputs_for_starter_weight_candidate

logger = logging.getLogger("mlb_edge_analyzer.historical")

# Muestra mínima dentro de la ventana para confiar en el valor windowed --
# por debajo, se usa el acumulado de temporada (nunca un invento).
MIN_WINDOW_TEAM_PA = 150       # ~4-5 juegos de equipo
MIN_WINDOW_STARTER_IP = 8.0    # ~2 salidas de abridor
MIN_WINDOW_BULLPEN_IP = 15.0   # relevo agregado del equipo


def _window_bounds(as_of_date: str, window_days: int) -> tuple[str, str]:
    """[start, end) -- end EXCLUSIVO en as_of_date: el propio día del juego
    nunca entra (mismo criterio anti-fuga que point_in_time_provider.py,
    que corta en as_of_date - 1)."""
    end = date.fromisoformat(as_of_date)
    start = end - timedelta(days=window_days)
    return start.isoformat(), as_of_date


def windowed_team_ops(bat_rows: list, as_of_date: str, window_days: int) -> float | None:
    """OPS del equipo recalculado SOLO con los juegos dentro de la ventana.
    bat_rows: filas de HistoricalRawBattingLog de UN equipo/temporada.
    None si la muestra en ventana no llega a MIN_WINDOW_TEAM_PA."""
    start, end = _window_bounds(as_of_date, window_days)
    ab = h = doubles = triples = hr = bb = hbp = sf = 0
    for r in bat_rows:
        if not (start <= r.game_date < end):
            continue
        ab += r.at_bats or 0
        h += r.hits or 0
        doubles += r.doubles or 0
        triples += r.triples or 0
        hr += r.home_runs or 0
        bb += r.walks or 0
        hbp += r.hit_by_pitch or 0
        sf += r.sac_flies or 0

    pa = ab + bb + hbp + sf
    if pa < MIN_WINDOW_TEAM_PA or ab == 0:
        return None
    obp = (h + bb + hbp) / pa
    total_bases = h + doubles + 2 * triples + 3 * hr
    slg = total_bases / ab
    return obp + slg


def windowed_pitcher_era(pitch_rows: list, as_of_date: str, window_days: int) -> float | None:
    """ERA del pitcher SOLO con sus apariciones dentro de la ventana.
    pitch_rows: filas de HistoricalRawPitchingLog de UN pitcher/temporada.
    None si no llega a MIN_WINDOW_STARTER_IP entradas en la ventana."""
    start, end = _window_bounds(as_of_date, window_days)
    ip = er = 0.0
    for r in pitch_rows:
        if not (start <= r.game_date < end):
            continue
        ip += r.innings_pitched or 0.0
        er += r.earned_runs or 0
    if ip < MIN_WINDOW_STARTER_IP:
        return None
    return 9.0 * er / ip


def windowed_bullpen_era(roster_pitcher_ids: list, logs_by_pitcher: dict,
                          as_of_date: str, window_days: int) -> float | None:
    """ERA agregado del bullpen (roster activo en la fecha del juego, mismo
    snapshot que usó la ingesta) con las apariciones de esos pitchers dentro
    de la ventana. Equivale al promedio ponderado por IP que usa
    point_in_time_provider.py::bullpen_era_as_of (era_i*ip_i == 9*er_i).
    None si el agregado no llega a MIN_WINDOW_BULLPEN_IP."""
    start, end = _window_bounds(as_of_date, window_days)
    total_ip = total_er = 0.0
    for pid in roster_pitcher_ids:
        for r in logs_by_pitcher.get(pid, ()):
            if not (start <= r.game_date < end):
                continue
            total_ip += r.innings_pitched or 0.0
            total_er += r.earned_runs or 0
    if total_ip < MIN_WINDOW_BULLPEN_IP:
        return None
    return 9.0 * total_er / total_ip


def _blend(season_value: float | None, window_value: float | None, weight: float) -> float | None:
    """weight * ventana + (1-weight) * acumulado. Si la ventana no tiene
    muestra suficiente (None), cae al acumulado tal cual."""
    if season_value is None:
        return None
    if window_value is None:
        return season_value
    return weight * window_value + (1.0 - weight) * season_value


def evaluate_recent_form(
    season_year: int, run_id: int, window_days: int,
    blend_weights: list[float] | None = None, session_factory=None,
) -> dict:
    """
    Experimento completo sobre una temporada ya ingerida: Brier de Skellam
    con insumos mezclados (ventana de `window_days` con peso w) contra el
    baseline w=0.0 (acumulado puro, lo que producción usa hoy). Requiere
    que raw_ingestion.py ya haya poblado los logs crudos de esta temporada.

    Nota de comparabilidad: el baseline se RE-CALCULA aquí con el mismo
    pathway (predict_from_raw_inputs con league defaults, ver nota en
    training.py) en vez de leer HistoricalPrediction -- así baseline y
    variantes comparten exactamente los mismos supuestos y la única
    diferencia es la mezcla de forma reciente.
    """
    blend_weights = blend_weights or [0.3, 0.5, 0.7, 1.0]
    session_factory = session_factory or SessionLocal
    session = session_factory()
    try:
        games = {
            g.game_pk: g for g in
            session.query(HistoricalGame).filter_by(run_id=run_id, season_year=season_year).all()
        }
        analyses = session.query(HistoricalAnalysis).filter_by(
            run_id=run_id, season_year=season_year,
        ).all()

        bat_by_team = defaultdict(list)
        for r in session.query(HistoricalRawBattingLog).filter_by(season_year=season_year).all():
            bat_by_team[r.team_id].append(r)
        pitch_by_pitcher = defaultdict(list)
        for r in session.query(HistoricalRawPitchingLog).filter_by(season_year=season_year).all():
            pitch_by_pitcher[r.pitcher_id].append(r)
        roster_by_team_date = defaultdict(list)
        for r in session.query(HistoricalRawRosterSnapshot).filter_by(season_year=season_year).all():
            roster_by_team_date[(r.team_id, r.as_of_date)].append(r.pitcher_id)

        def _prob_rows(weight: float | None):
            """(probs, actuals) con mezcla de peso `weight`; None = baseline
            (acumulado puro, sin tocar ningún insumo)."""
            probs, actuals = [], []
            for a in analyses:
                g = games.get(a.game_pk)
                if g is None or g.winner is None:
                    continue
                raw = _raw_inputs_for_starter_weight_candidate(a, STARTER_WEIGHT)
                if raw is None:
                    continue
                if weight is not None:
                    away_ops_w = windowed_team_ops(bat_by_team.get(g.away_team_id, ()), a.game_date, window_days)
                    home_ops_w = windowed_team_ops(bat_by_team.get(g.home_team_id, ()), a.game_date, window_days)
                    away_era_w = windowed_pitcher_era(pitch_by_pitcher.get(g.away_pitcher_id, ()), a.game_date, window_days)
                    home_era_w = windowed_pitcher_era(pitch_by_pitcher.get(g.home_pitcher_id, ()), a.game_date, window_days)
                    away_bp_w = windowed_bullpen_era(
                        roster_by_team_date.get((g.away_team_id, a.game_date), ()),
                        pitch_by_pitcher, a.game_date, window_days)
                    home_bp_w = windowed_bullpen_era(
                        roster_by_team_date.get((g.home_team_id, a.game_date), ()),
                        pitch_by_pitcher, a.game_date, window_days)

                    raw.update({
                        "away_ops": _blend(a.away_ops, away_ops_w, weight),
                        "home_ops": _blend(a.home_ops, home_ops_w, weight),
                        "away_era": _blend(a.away_era, away_era_w, weight),
                        "home_era": _blend(a.home_era, home_era_w, weight),
                        "away_bullpen_era": _blend(a.away_bullpen_era, away_bp_w, weight),
                        "home_bullpen_era": _blend(a.home_bullpen_era, home_bp_w, weight),
                    })
                prediction = predict_from_raw_inputs(raw)
                probs.append(prediction["home_skellam_prob"])
                actuals.append(1 if g.winner == "home" else 0)
            return probs, actuals

        baseline_probs, baseline_actuals = _prob_rows(None)
        baseline_brier = brier_score(baseline_probs, baseline_actuals)
        baseline_n = len(baseline_probs)

        proposals = []
        for weight in blend_weights:
            probs, actuals = _prob_rows(weight)
            candidate_brier = brier_score(probs, actuals)
            improved = (
                candidate_brier is not None and baseline_brier is not None and candidate_brier < baseline_brier
            )
            sim = HistoricalSimulation(
                run_id=run_id, season_year=season_year,
                param_name=f"RECENT_FORM_BLEND_W{window_days}D",
                baseline_value=0.0, proposed_value=weight,
                based_on_metric="brier_score", baseline_metric_value=baseline_brier,
                proposed_metric_value=candidate_brier, improved=improved,
                notes=(
                    f"n={len(probs)} juegos con resultado real en {season_year}. Ventana de "
                    f"{window_days} días con peso {weight} sobre OPS/ERA abridor/ERA bullpen "
                    f"(fallback al acumulado si la ventana no tiene muestra mínima). Generado "
                    f"por historical_engine/recent_form.py -- NO aplicado a producción."
                ),
                applied=False,
            )
            session.add(sim)
            proposals.append({
                "blend_weight": weight, "brier_score": candidate_brier,
                "n_sample": len(probs), "improved_over_baseline": improved,
            })
        session.commit()
    finally:
        session.close()

    best = min(
        (p for p in proposals if p["brier_score"] is not None),
        key=lambda p: p["brier_score"], default=None,
    )

    return {
        "season_year": season_year, "window_days": window_days,
        "baseline_brier_score": baseline_brier, "baseline_n_sample": baseline_n,
        "proposals": proposals,
        "best_candidate": best,
        "applied": False,
        "note": "Nada se aplicó a producción -- producción sigue usando el acumulado de temporada.",
    }
