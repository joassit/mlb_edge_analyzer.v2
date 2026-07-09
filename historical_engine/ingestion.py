"""
Ingesta histórica: trae el calendario y los resultados finales de juegos ya
jugados, y los guarda ÚNICAMENTE en historical_engine.db.HistoricalGame.

Reutiliza data/mlb_api.py::get_schedule()/get_game_result() -- son
funciones de LECTURA pura contra una API externa, sin caché ni estado
compartido con nada de producción, así que reusarlas no crea ninguna ruta
de contaminación (a diferencia de data/stats.py, ver
point_in_time_provider.py). Nunca importa ni toca db/database.py.
"""

import logging
from datetime import date, timedelta

from data.mlb_api import get_schedule, get_game_result
from historical_engine.db import HistoricalGame, SessionLocal, init_historical_db

logger = logging.getLogger("mlb_edge_analyzer.historical")

# Fechas de inicio/fin APROXIMADAS de temporada regular -- suficientes para
# acotar un backtest (un puñado de días de más/menos en los bordes no
# afecta el análisis estadístico), documentadas como aproximación a
# propósito en vez de presentarse como fechas oficiales exactas.
SEASON_DATE_RANGES = {
    2023: (date(2023, 3, 30), date(2023, 10, 1)),
    2024: (date(2024, 3, 20), date(2024, 9, 29)),
    2025: (date(2025, 3, 27), date(2025, 9, 28)),
    2026: (date(2026, 3, 26), date(2026, 9, 27)),
}


def _daterange(start: date, end: date):
    n = (end - start).days
    for i in range(n + 1):
        yield start + timedelta(days=i)


def ingest_date_range(start: date, end: date, run_id: int, season: int) -> dict:
    """
    Trae el calendario día por día entre start y end (inclusive) y guarda
    cada juego en HistoricalGame -- con resultado final si `get_game_result`
    ya lo tiene disponible (nunca lo inventa: None si el juego no terminó
    o la API falla, igual que en producción).

    HistoricalGame tiene UNIQUE(game_pk, run_id) -- necesario porque un
    juego suspendido/reprogramado puede aparecer en el calendario oficial
    de MÁS DE UN día dentro del mismo rango (el día original y el día de
    reanudación/makeup), y sin este guard esto rompía ingest_date_range()
    a mitad de una corrida real (ver UNIQUE constraint failed:
    historical_game.game_pk, historical_game.run_id, confirmado corriendo
    2024-05 completo contra la API real). game_pk repetido se salta -- se
    queda con la primera fecha vista, nunca se sobreescribe ni se inventa
    cuál de las dos fechas es la "correcta".

    Devuelve un resumen {"n_games": int, "n_final": int, "n_errors": int,
    "n_duplicate_game_pk_skipped": int}.
    """
    init_historical_db()
    session = SessionLocal()
    n_games = n_final = n_errors = n_duplicates = 0
    seen_game_pks = {
        row.game_pk for row in session.query(HistoricalGame.game_pk).filter_by(run_id=run_id).all()
    }
    try:
        for d in _daterange(start, end):
            games = get_schedule(d)
            for g in games:
                game_pk = g["game_pk"]
                if game_pk in seen_game_pks:
                    n_duplicates += 1
                    logger.info(f"[historical] game_pk={game_pk} ya ingerido para run_id={run_id} "
                                f"(visto de nuevo el {d}) -- se salta, probable juego suspendido/reprogramado.")
                    continue
                try:
                    result = None
                    if g.get("abstract_state") == "Final":
                        result = get_game_result(game_pk)

                    session.add(HistoricalGame(
                        run_id=run_id,
                        game_pk=game_pk,
                        game_date=g.get("game_date_official") or d.strftime("%Y-%m-%d"),
                        season_year=season,
                        away_team=g["away_team"], home_team=g["home_team"],
                        away_team_id=g.get("away_team_id"), home_team_id=g.get("home_team_id"),
                        away_pitcher_id=g.get("away_pitcher_id"), home_pitcher_id=g.get("home_pitcher_id"),
                        away_pitcher_name=g.get("away_pitcher_name"), home_pitcher_name=g.get("home_pitcher_name"),
                        status=g.get("status"),
                        home_score=result["home_score"] if result else None,
                        away_score=result["away_score"] if result else None,
                        winner=result["winner"] if result else None,
                        total_runs=result["total_runs"] if result else None,
                    ))
                    seen_game_pks.add(game_pk)
                    n_games += 1
                    if result is not None:
                        n_final += 1
                except Exception as e:
                    n_errors += 1
                    logger.error(f"[historical] error ingiriendo game_pk={game_pk} de {d}: {e}", exc_info=True)
            session.commit()
    finally:
        session.close()

    return {"n_games": n_games, "n_final": n_final, "n_errors": n_errors, "n_duplicate_game_pk_skipped": n_duplicates}


def ingest_season(season: int, run_id: int) -> dict:
    if season not in SEASON_DATE_RANGES:
        raise ValueError(f"Temporada {season} no está en SEASON_DATE_RANGES -- agrégala explícitamente antes de usarla.")
    start, end = SEASON_DATE_RANGES[season]
    return ingest_date_range(start, end, run_id, season)


def ingest_month(season: int, month: int, run_id: int) -> dict:
    start = date(season, month, 1)
    end = date(season, month + 1, 1) - timedelta(days=1) if month < 12 else date(season, 12, 31)
    season_start, season_end = SEASON_DATE_RANGES.get(season, (start, end))
    start = max(start, season_start)
    end = min(end, season_end)
    return ingest_date_range(start, end, run_id, season)
