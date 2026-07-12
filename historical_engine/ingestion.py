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
    # 2022 arrancó tarde por el lockout de la MLB (acuerdo colectivo
    # resuelto en marzo de 2022) -- Opening Day real fue el 7 de abril,
    # no la fecha habitual de finales de marzo de las otras temporadas.
    2022: (date(2022, 4, 7), date(2022, 10, 5)),
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
    2024-05 completo contra la API real).

    Un game_pk repetido nunca crea una segunda fila -- pero si la fila
    YA existente todavía no tiene resultado (winner is None: el día que se
    ingirió por primera vez, el juego estaba pospuesto/en curso) y esta
    reaparición SÍ trae abstract_state=Final, se reconcilia el resultado
    en la fila existente. El game_date NUNCA se toca en la reconciliación
    -- as_of_date se calcula desde la fecha ORIGINAL programada (cuándo
    debía jugarse el partido), no desde la fecha de reanudación, que es un
    accidente de calendario y correrla movería el corte point-in-time.
    Antes de este fix, un juego pospuesto quedaba con winner=None para
    siempre (se descartaba de accuracy/Brier sin que quedara registrado en
    ningún lado que existía un resultado real sin reconciliar).

    Devuelve un resumen {"n_games": int, "n_final": int, "n_errors": int,
    "n_duplicate_game_pk_skipped": int, "n_postponed_reconciled": int}.
    """
    init_historical_db()
    session = SessionLocal()
    n_games = n_final = n_errors = n_duplicates = n_reconciled = 0
    existing_by_pk = {
        row.game_pk: row for row in session.query(HistoricalGame).filter_by(run_id=run_id).all()
    }
    try:
        for d in _daterange(start, end):
            games = get_schedule(d)
            for g in games:
                game_pk = g["game_pk"]
                existing = existing_by_pk.get(game_pk)
                if existing is not None:
                    n_duplicates += 1
                    if existing.winner is None and g.get("abstract_state") == "Final":
                        try:
                            result = get_game_result(game_pk)
                        except Exception as e:
                            n_errors += 1
                            logger.error(f"[historical] error reconciliando game_pk={game_pk} "
                                         f"visto de nuevo el {d}: {e}", exc_info=True)
                            continue
                        if result:
                            existing.home_score = result["home_score"]
                            existing.away_score = result["away_score"]
                            existing.winner = result["winner"]
                            existing.total_runs = result["total_runs"]
                            existing.status = "Final"
                            n_reconciled += 1
                            logger.info(f"[historical] game_pk={game_pk}: resultado reconciliado desde "
                                        f"la fecha de reanudación ({d}) -- game_date original "
                                        f"({existing.game_date}) preservado.")
                    else:
                        logger.info(f"[historical] game_pk={game_pk} ya ingerido para run_id={run_id} "
                                    f"(visto de nuevo el {d}) -- se salta, ya tenía resultado o sigue sin Final.")
                    continue
                try:
                    result = None
                    if g.get("abstract_state") == "Final":
                        result = get_game_result(game_pk)

                    new_row = HistoricalGame(
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
                    )
                    session.add(new_row)
                    existing_by_pk[game_pk] = new_row
                    n_games += 1
                    if result is not None:
                        n_final += 1
                except Exception as e:
                    n_errors += 1
                    logger.error(f"[historical] error ingiriendo game_pk={game_pk} de {d}: {e}", exc_info=True)
            session.commit()
    finally:
        session.close()

    return {
        "n_games": n_games, "n_final": n_final, "n_errors": n_errors,
        "n_duplicate_game_pk_skipped": n_duplicates, "n_postponed_reconciled": n_reconciled,
    }


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
