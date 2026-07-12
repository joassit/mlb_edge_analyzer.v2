"""
Pipeline histórico -- orquesta ingesta + reconstrucción point-in-time +
predicción (reutilizando model/predictor.py::predict_from_raw_inputs, una
función PURA sin estado ni DB, el mismo motor de cálculo que usa
producción, para que un juego histórico y uno en vivo nunca puedan
calcularse con matemática distinta) + almacenamiento en las tablas de
historical_engine.db.

Nunca importa db.database ni tracking.results_tracker. Nunca escribe en
ninguna tabla de producción -- ver tests/test_historical_isolation.py.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from config import (
    STARTER_WEIGHT, HOME_FIELD_ADVANTAGE, PARK_FACTOR_WEIGHT,
    WEATHER_CORRECTION, NEGBIN_DISPERSION, MODEL_VERSION,
    SKELLAM_SHRINKAGE_ALPHA,
)
from model.predictor import predict_from_raw_inputs
from version_info import get_git_commit

from historical_engine.db import (
    HistoricalRun, HistoricalGame, HistoricalAnalysis, HistoricalPrediction,
    SessionLocal, init_historical_db,
)
from historical_engine.ingestion import ingest_date_range, ingest_season, ingest_month, SEASON_DATE_RANGES
from historical_engine.point_in_time_stats import (
    reconstruct_game_features, default_as_of_date, LookAheadBiasError,
)
from historical_engine.point_in_time_provider import HistoricalStatsProvider, MLBStatsAPIProvider

logger = logging.getLogger("mlb_edge_analyzer.historical")

_SOURCES = ("heuristic", "skellam", "negbin")


@dataclass
class PipelineResult:
    run_id: int
    n_games: int = 0
    n_analyzed: int = 0
    n_skipped_missing_pitcher: int = 0
    n_errors: int = 0
    errors: list = field(default_factory=list)


def _start_run(run_type: str, scope_description: str, season_year: int | None) -> int:
    init_historical_db()
    session = SessionLocal()
    try:
        run = HistoricalRun(
            run_type=run_type, scope_description=scope_description, season_year=season_year,
            model_version=MODEL_VERSION, git_commit=get_git_commit(), status="running",
        )
        session.add(run)
        session.commit()
        return run.id
    finally:
        session.close()


def _finish_run(run_id: int, status: str, n_games: int, n_skipped: int, n_errors: int) -> None:
    from datetime import datetime, timezone
    session = SessionLocal()
    try:
        run = session.query(HistoricalRun).filter_by(id=run_id).first()
        if run is not None:
            run.status = status
            run.n_games_processed = n_games
            run.n_games_skipped = n_skipped
            run.n_errors = n_errors
            run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.commit()
    finally:
        session.close()


def _analyze_and_store_game(hg: HistoricalGame, provider: HistoricalStatsProvider, session) -> bool:
    """Reconstruye features + predicciones de un HistoricalGame ya ingerido
    y guarda HistoricalAnalysis + una HistoricalPrediction por motor.
    Devuelve True si se analizó, False si se saltó (pitcher no confirmado,
    ver mismo criterio que main.py::analyze_today() para no generar una
    predicción sin abridor confirmado)."""
    if not hg.away_pitcher_id or not hg.home_pitcher_id:
        return False

    as_of = default_as_of_date(hg.game_date)
    game = {
        "game_date": hg.game_date,
        "away_team_id": hg.away_team_id, "home_team_id": hg.home_team_id,
        "away_pitcher_id": hg.away_pitcher_id, "home_pitcher_id": hg.home_pitcher_id,
    }
    try:
        features = reconstruct_game_features(game, as_of_date=as_of, season=hg.season_year, provider=provider)
    except LookAheadBiasError:
        logger.error(f"[historical] game_pk={hg.game_pk}: fecha de corte inválida, se omite")
        raise

    required = ("away_era", "home_era", "away_ops", "home_ops", "away_bullpen_era", "home_bullpen_era")
    if any(features.get(k) is None for k in required):
        return False  # sin datos suficientes point-in-time -- no se inventa un fallback silencioso

    raw_inputs = {
        **features,
        "league_ops": features.get("league_ops") or 0.750,
        "league_era": features.get("league_era") or 4.30,
        "league_avg_runs_per_game": features.get("league_avg_runs_per_game") or 4.5,
        "starter_weight": STARTER_WEIGHT,
        "home_field_advantage": HOME_FIELD_ADVANTAGE,
        "park_factor_weight": PARK_FACTOR_WEIGHT,
        "weather_correction": WEATHER_CORRECTION,
        "negbin_dispersion": NEGBIN_DISPERSION,
        # Ojo para futuros barridos de calibración: con esto, las corridas
        # históricas nuevas persisten el Skellam YA CONTRAÍDO -- ajustar
        # alpha de nuevo contra esas filas lo doble-contraería. Los barridos
        # de propose_probability_shrinkage deben correrse contra ingestas
        # hechas con alpha=1.0, o des-contraer primero.
        "skellam_shrinkage_alpha": SKELLAM_SHRINKAGE_ALPHA,
    }
    prediction = predict_from_raw_inputs(raw_inputs)

    analysis = HistoricalAnalysis(
        run_id=hg.run_id, game_pk=hg.game_pk, game_date=hg.game_date, season_year=hg.season_year,
        as_of_date=as_of,
        away_era=features["away_era"], home_era=features["home_era"],
        away_innings_pitched=features.get("away_innings_pitched"),
        home_innings_pitched=features.get("home_innings_pitched"),
        away_ops=features["away_ops"], home_ops=features["home_ops"],
        away_team_pa=features.get("away_team_pa"), home_team_pa=features.get("home_team_pa"),
        away_bullpen_era=features["away_bullpen_era"], home_bullpen_era=features["home_bullpen_era"],
        away_k_pct=features.get("away_k_pct"), home_k_pct=features.get("home_k_pct"),
        away_bb_pct=features.get("away_bb_pct"), home_bb_pct=features.get("home_bb_pct"),
        away_days_rest=features.get("away_days_rest"), home_days_rest=features.get("home_days_rest"),
        park_factor=features.get("park_factor"), park_name=features.get("park_name"),
        temp_f=features.get("temp_f"),
        away_proj_runs=prediction["away_proj_runs"], home_proj_runs=prediction["home_proj_runs"],
        away_model_prob=prediction["away_model_prob"], home_model_prob=prediction["home_model_prob"],
        away_skellam_prob=prediction["away_skellam_prob"], home_skellam_prob=prediction["home_skellam_prob"],
        away_negbin_prob=prediction["away_negbin_prob"], home_negbin_prob=prediction["home_negbin_prob"],
    )
    session.add(analysis)

    actual_winner = hg.winner  # None si el juego no tiene resultado final todavía
    prob_by_source = {
        "heuristic": (prediction["away_model_prob"], prediction["home_model_prob"]),
        "skellam": (prediction["away_skellam_prob"], prediction["home_skellam_prob"]),
        "negbin": (prediction["away_negbin_prob"], prediction["home_negbin_prob"]),
    }
    for source, (away_p, home_p) in prob_by_source.items():
        predicted_winner = "home" if home_p > away_p else "away"
        correct = (predicted_winner == actual_winner) if actual_winner else None
        session.add(HistoricalPrediction(
            run_id=hg.run_id, game_pk=hg.game_pk, game_date=hg.game_date, season_year=hg.season_year,
            source=source, away_prob=away_p, home_prob=home_p,
            predicted_winner=predicted_winner, actual_winner=actual_winner, correct=correct,
        ))
    return True


def _run_over_games(run_id: int, season: int, provider: HistoricalStatsProvider | None) -> PipelineResult:
    provider = provider or MLBStatsAPIProvider()
    session = SessionLocal()
    result = PipelineResult(run_id=run_id)
    try:
        games = session.query(HistoricalGame).filter_by(run_id=run_id).all()
        result.n_games = len(games)
        for hg in games:
            try:
                analyzed = _analyze_and_store_game(hg, provider, session)
                if analyzed:
                    result.n_analyzed += 1
                else:
                    result.n_skipped_missing_pitcher += 1
            except LookAheadBiasError as e:
                result.n_errors += 1
                result.errors.append(str(e))
            except Exception as e:
                result.n_errors += 1
                result.errors.append(f"game_pk={hg.game_pk}: {e}")
                logger.error(f"[historical] error analizando game_pk={hg.game_pk}: {e}", exc_info=True)
        session.commit()
    finally:
        session.close()

    status = "completed" if result.n_errors == 0 else "completed_with_errors"
    _finish_run(run_id, status, result.n_analyzed, result.n_skipped_missing_pitcher, result.n_errors)
    return result


def run_season(season: int, provider: HistoricalStatsProvider | None = None) -> PipelineResult:
    run_id = _start_run("season", f"temporada {season} completa", season)
    ingest_season(season, run_id)
    return _run_over_games(run_id, season, provider)


def run_month(season: int, month: int, provider: HistoricalStatsProvider | None = None) -> PipelineResult:
    run_id = _start_run("month", f"{season}-{month:02d}", season)
    ingest_month(season, month, run_id)
    return _run_over_games(run_id, season, provider)


def run_date_range(start: date, end: date, season: int, provider: HistoricalStatsProvider | None = None) -> PipelineResult:
    run_id = _start_run("date_range", f"{start} a {end}", season)
    ingest_date_range(start, end, run_id, season)
    return _run_over_games(run_id, season, provider)


def run_team(team_id: int, season: int, provider: HistoricalStatsProvider | None = None) -> PipelineResult:
    """Ingesta la temporada completa y filtra a los juegos de un equipo --
    la MLB Stats API no ofrece un calendario 'solo de un equipo' sin
    también traer todo el resto de la fecha, así que se reutiliza la
    ingesta de temporada y se descartan (no se analizan, no se guardan
    predicciones para) los juegos donde el equipo no participó."""
    run_id = _start_run("team", f"equipo {team_id}, temporada {season}", season)
    ingest_season(season, run_id)

    session = SessionLocal()
    try:
        others = (
            session.query(HistoricalGame)
            .filter_by(run_id=run_id)
            .filter(HistoricalGame.away_team_id != team_id, HistoricalGame.home_team_id != team_id)
            .all()
        )
        for g in others:
            session.delete(g)
        session.commit()
    finally:
        session.close()

    return _run_over_games(run_id, season, provider)


def run_pitcher(pitcher_id: int, season: int, provider: HistoricalStatsProvider | None = None) -> PipelineResult:
    run_id = _start_run("pitcher", f"pitcher {pitcher_id}, temporada {season}", season)
    ingest_season(season, run_id)

    session = SessionLocal()
    try:
        others = (
            session.query(HistoricalGame)
            .filter_by(run_id=run_id)
            .filter(HistoricalGame.away_pitcher_id != pitcher_id, HistoricalGame.home_pitcher_id != pitcher_id)
            .all()
        )
        for g in others:
            session.delete(g)
        session.commit()
    finally:
        session.close()

    return _run_over_games(run_id, season, provider)


def run_single_game(game_pk: int, game_date: str, season: int,
                     provider: HistoricalStatsProvider | None = None) -> PipelineResult:
    from data.mlb_api import get_game_result, get_schedule

    run_id = _start_run("game", f"game_pk={game_pk}", season)
    d = date.fromisoformat(game_date)
    games = get_schedule(d)
    match = next((g for g in games if g["game_pk"] == game_pk), None)
    if match is None:
        _finish_run(run_id, "failed", 0, 0, 1)
        return PipelineResult(run_id=run_id, n_errors=1, errors=[f"game_pk={game_pk} no encontrado en {game_date}"])

    result = get_game_result(game_pk) if match.get("abstract_state") == "Final" else None
    session = SessionLocal()
    try:
        session.add(HistoricalGame(
            run_id=run_id, game_pk=game_pk, game_date=match.get("game_date_official") or game_date,
            season_year=season, away_team=match["away_team"], home_team=match["home_team"],
            away_team_id=match.get("away_team_id"), home_team_id=match.get("home_team_id"),
            away_pitcher_id=match.get("away_pitcher_id"), home_pitcher_id=match.get("home_pitcher_id"),
            away_pitcher_name=match.get("away_pitcher_name"), home_pitcher_name=match.get("home_pitcher_name"),
            status=match.get("status"),
            home_score=result["home_score"] if result else None,
            away_score=result["away_score"] if result else None,
            winner=result["winner"] if result else None,
            total_runs=result["total_runs"] if result else None,
        ))
        session.commit()
    finally:
        session.close()

    return _run_over_games(run_id, season, provider)
