"""Ingesta MINIMA de Statcast -- Etapa 2 del plan acordado con el usuario
(ver `jsa/docs/statcast_integration_design.md`). Alcance deliberadamente
acotado: SOLO 2 campos crudos por evento de bateo (`launch_speed` y
`estimated_woba_using_speedangle`), de los cuales se derivan localmente
las 4 hipotesis H1-H4 en `statcast_candidate_audit.py` -- nunca se
incorporan mas metricas "por si acaso".

Arquitectura deliberada: bulk-pull de eventos crudos LIGA COMPLETA (sin
filtro de equipo) por ventanas de fecha, guardados tal cual en
`historical_statcast_event`, sin ninguna agregacion point-in-time en esta
etapa -- exactamente el mismo patron que `historical_game` (se ingiere el
hecho crudo una vez, la reconstruccion point-in-time-safe se hace despues
en Python, dia por dia). Esto es deliberadamente MAS BARATO que replicar
el patron de Trend (una llamada HTTP por equipo por juego): el mismo
descubrimiento del spike de factibilidad (Etapa 1) mostro que una sola
consulta sin filtro de equipo trae los datos de TODA la liga para esa
ventana de fechas.

El semantica de `game_date_gt`/`game_date_lt` de Baseball Savant es
INCLUSIVA en ambos extremos (confirmado empiricamente en el spike -- ver
ROADMAP.md) -- esto NO genera leakage aca porque esta ingesta nunca
calcula una feature point-in-time directamente contra la API; solo trae
HECHOS crudos historicos por lotes de fecha. El leakage se previene en la
etapa de RECONSTRUCCION (`statcast_candidate_audit.py`), que filtra
`game_date < corte` en Python sobre datos YA almacenados -- mismo
principio que rige `historical_game`+Elo/Pythagorean/head-to-head."""

from __future__ import annotations

import csv
import io
import logging
import time
from datetime import date, timedelta

import requests

from jsa.historical import db as historical_db
from jsa.historical.ingestion import season_date_range

logger = logging.getLogger("jsa.historical")

STATCAST_BASE = "https://baseballsavant.mlb.com"
STATCAST_REQUEST_TIMEOUT = 60
DEFAULT_CHUNK_DAYS = 30


def _search_csv_params(*, season: int, game_date_gt: str, game_date_lt: str) -> dict:
    """SIN filtro de `team` -- trae liga completa para la ventana de
    fechas, mismo descubrimiento del spike de factibilidad que hace esta
    ingesta mucho mas barata que una llamada por equipo."""
    return {
        "all": "true",
        "hfGT": "R|",
        "hfSea": f"{season}|",
        "player_type": "batter",
        "game_date_gt": game_date_gt,
        "game_date_lt": game_date_lt,
        "min_pitches": "0",
        "min_results": "0",
        "group_by": "name",
        "sort_col": "pitches",
        "player_event_type": "batter",
        "sort_order": "desc",
        "min_pas": "0",
        "type": "details",
    }


def _date_chunks(start: str, end: str, chunk_days: int) -> list[tuple[str, str]]:
    """Ventanas NO solapadas (ambos extremos de la fuente son inclusivos,
    confirmado en el spike de factibilidad -- se avanza 1 dia entre
    chunks para no traer el mismo dia dos veces)."""
    chunks = []
    current = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    while current <= end_date:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end_date)
        chunks.append((current.isoformat(), chunk_end.isoformat()))
        current = chunk_end + timedelta(days=1)
    return chunks


def _parse_batted_ball_events(csv_text: str) -> list[dict]:
    """Filtra a eventos de bateo reales (`type == 'X'`, bola puesta en
    juego) -- descarta el resto (bolas, strikes cantados) que no traen
    `launch_speed`/`estimated_woba_using_speedangle` utiles."""
    reader = csv.DictReader(io.StringIO(csv_text))
    events = []
    for row in reader:
        if row.get("type") != "X":
            continue
        launch_speed = row.get("launch_speed")
        xwoba = row.get("estimated_woba_using_speedangle")
        try:
            game_pk = int(row["game_pk"])
            at_bat_number = int(row["at_bat_number"])
            pitch_number = int(row["pitch_number"])
            game_date = date.fromisoformat(row["game_date"])
        except (KeyError, ValueError, TypeError):
            continue  # fila sin identificadores validos -- se descarta, no se aborta el lote
        events.append({
            "game_pk": game_pk,
            "game_date": game_date,
            "at_bat_number": at_bat_number,
            "pitch_number": pitch_number,
            "inning_topbot": row.get("inning_topbot") or None,
            "batter_id": int(row["batter"]) if row.get("batter") else None,
            "pitcher_id": int(row["pitcher"]) if row.get("pitcher") else None,
            "launch_speed": float(launch_speed) if launch_speed not in (None, "") else None,
            "xwoba": float(xwoba) if xwoba not in (None, "") else None,
        })
    return events


def fetch_batted_ball_events_for_range(season: int, game_date_gt: str, game_date_lt: str) -> tuple[list[dict], dict]:
    """Un unico GET liga-completa para la ventana -- devuelve (eventos,
    metricas_de_costo_del_chunk). Nunca propaga excepcion: un chunk
    fallido se reporta con `error` y lista vacia, la temporada sigue con
    los demas chunks (mismo criterio de resiliencia que `fetch_season_games`)."""
    start = time.monotonic()
    cost = {"game_date_gt": game_date_gt, "game_date_lt": game_date_lt}
    try:
        resp = requests.get(
            f"{STATCAST_BASE}/statcast_search/csv",
            params=_search_csv_params(season=season, game_date_gt=game_date_gt, game_date_lt=game_date_lt),
            timeout=STATCAST_REQUEST_TIMEOUT,
            headers={"User-Agent": "jsa-statcast-minimal-ingest/1.0"},
        )
        resp.raise_for_status()
        elapsed = time.monotonic() - start
        events = _parse_batted_ball_events(resp.text)
        cost.update({
            "elapsed_seconds": round(elapsed, 3),
            "response_bytes": len(resp.content),
            "n_batted_ball_events": len(events),
            "error": None,
        })
        return events, cost
    except requests.RequestException as e:
        cost.update({"elapsed_seconds": round(time.monotonic() - start, 3), "response_bytes": 0, "n_batted_ball_events": 0, "error": str(e)})
        logger.warning("Statcast: fallo el chunk %s a %s de la temporada %s: %s", game_date_gt, game_date_lt, season, e)
        return [], cost


def ingest_statcast_season_minimal(season: int, historical_database_url: str, *, chunk_days: int = DEFAULT_CHUNK_DAYS, force: bool = False) -> dict:
    """Ingesta minima de una temporada -- devuelve un resumen con las
    metricas de costo exigidas explicitamente por el usuario (tiempo,
    volumen, almacenamiento) para poder compararlas contra el beneficio
    predictivo obtenido en `statcast_candidate_audit.py`."""
    engine = historical_db.get_engine(historical_database_url)
    historical_db.init_historical_storage(engine)

    existing = historical_db.count_statcast_events_for_season(engine, season)
    if existing > 0 and not force:
        logger.info("Statcast temporada %s ya ingerida (%d eventos) -- se salta (usar force=True para re-ingerir).", season, existing)
        return {"season": season, "already_ingested": existing, "skipped": True}

    if force and existing > 0:
        deleted = historical_db.clear_statcast_season(engine, season)
        logger.warning("Statcast temporada %s: force=True -- borrados %d eventos antes de re-ingerir.", season, deleted)

    start_date, end_date = season_date_range(season)
    chunks = _date_chunks(start_date, end_date, chunk_days)

    total_start = time.monotonic()
    all_events: list[dict] = []
    chunk_costs: list[dict] = []
    for game_date_gt, game_date_lt in chunks:
        events, cost = fetch_batted_ball_events_for_range(season, game_date_gt, game_date_lt)
        all_events.extend(events)
        chunk_costs.append(cost)

    n_attempted = historical_db.bulk_insert_statcast_events(engine, season, all_events)
    n_actually_stored = historical_db.count_statcast_events_for_season(engine, season)
    total_elapsed = time.monotonic() - total_start

    summary = {
        "season": season,
        "already_ingested": 0,
        "skipped": False,
        "n_chunks": len(chunks),
        "n_chunks_with_error": sum(1 for c in chunk_costs if c.get("error")),
        "n_batted_ball_events_fetched": len(all_events),
        "n_rows_insert_attempted": n_attempted,
        "n_rows_actually_stored": n_actually_stored,  # despues de ignorar duplicados via ON CONFLICT DO NOTHING
        "total_elapsed_seconds": round(total_elapsed, 3),
        "total_response_bytes": sum(c.get("response_bytes", 0) for c in chunk_costs),
        "chunk_costs": chunk_costs,
    }
    logger.info(
        "ingest_statcast_season_minimal(%s) completo -- %d eventos, %d chunks (%d con error), %.1fs, %d bytes",
        season, n_actually_stored, len(chunks), summary["n_chunks_with_error"], total_elapsed, summary["total_response_bytes"],
    )
    return summary
