"""
Ingesta de LOGS CRUDOS juego-por-juego (bateo de equipo, pitcheo individual,
roster activo por fecha) -- una capa nueva y deliberadamente separada de
ingestion.py/pipeline.py (que reconstruyen features AGREGADAS point-in-time
y las descartan después de usarlas una vez).

Por qué existe esto: point_in_time_provider.py pide `stats=byDateRange` una
vez POR JUEGO (¿cómo se veía este equipo/pitcher ANTES de esta fecha?), lo
que implica cientos/miles de llamadas repetidas con rangos de fecha que se
solapan casi por completo -- y el resultado agregado se guarda en
HistoricalAnalysis, pero el detalle crudo se descarta. Si mañana queremos
probar "forma reciente" (últimos N días) en vez de "temporada completa
acumulada", no hay forma de recalcularlo sin volver a golpear la API.

Esta capa invierte eso: UNA sola llamada `stats=gameLog` por equipo/pitcher
por temporada (temporada ya cerrada = dato inmutable, se puede cachear para
siempre), guardada en HistoricalRawBattingLog/HistoricalRawPitchingLog. El
roster activo SÍ varía por fecha (trades/call-ups), así que ahí se cachea
un snapshot por cada fecha de corte realmente usada en HistoricalGame (el
mismo endpoint que ya usa point_in_time_provider.py::bullpen_era_as_of,
pero guardado en vez de descartado) en HistoricalRawRosterSnapshot.

Con las 3 tablas pobladas, CUALQUIER ventana (temporada completa o forma
reciente) para OPS/ERA/composición de bullpen se calcula con aritmética
local, sin volver a tocar la API para estas temporadas ya cerradas.

Idempotente por diseño: antes de pedir el gameLog de un equipo/pitcher (o
el roster de una fecha), revisa si ya hay filas para esa clave -- si el job
se corta a mitad de camino, una segunda corrida retoma donde quedó sin
re-descargar nada.
"""

import logging
from datetime import date, timedelta

import requests

from data.http import session as http_session
from historical_engine.config import MLB_API_BASE, INGESTION_REQUEST_TIMEOUT
from historical_engine.db import (
    HistoricalGame, HistoricalRawBattingLog, HistoricalRawPitchingLog,
    HistoricalRawRosterSnapshot, HistoricalRawFetchLedger, SessionLocal,
)

logger = logging.getLogger("mlb_edge_analyzer.historical")


def _http_get(url: str, params: dict) -> dict:
    resp = http_session.get(url, params=params, timeout=INGESTION_REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _parse_innings(ip_str) -> float | None:
    """Mismo parseo que data/stats.py::_parse_innings -- '63.1' son 63 y
    1/3 entradas, no 63.1 en base 10. Duplicado a propósito (cero riesgo de
    contaminación cruzada, ver point_in_time_provider.py)."""
    if ip_str is None:
        return None
    ip_str = str(ip_str)
    if "." not in ip_str:
        return float(ip_str)
    whole, frac = ip_str.split(".")
    thirds = {"0": 0, "1": 1, "2": 2}.get(frac, 0)
    return int(whole) + thirds / 3


def _team_ids_for_season(season_year: int, run_id: int, db_session) -> set[int]:
    games = db_session.query(HistoricalGame).filter_by(run_id=run_id, season_year=season_year).all()
    ids = set()
    for g in games:
        if g.away_team_id:
            ids.add(g.away_team_id)
        if g.home_team_id:
            ids.add(g.home_team_id)
    return ids


def _starter_pitcher_ids_for_season(season_year: int, run_id: int, db_session) -> set[int]:
    games = db_session.query(HistoricalGame).filter_by(run_id=run_id, season_year=season_year).all()
    ids = set()
    for g in games:
        if g.away_pitcher_id:
            ids.add(g.away_pitcher_id)
        if g.home_pitcher_id:
            ids.add(g.home_pitcher_id)
    return ids


def _already_fetched(db_session, entity_type: str, entity_key: str, season_year: int) -> bool:
    return db_session.query(HistoricalRawFetchLedger).filter_by(
        entity_type=entity_type, entity_key=entity_key, season_year=season_year,
    ).first() is not None


def _mark_fetched(db_session, entity_type: str, entity_key: str, season_year: int) -> None:
    db_session.add(HistoricalRawFetchLedger(
        entity_type=entity_type, entity_key=entity_key, season_year=season_year,
    ))


def _distinct_team_as_of_dates(season_year: int, run_id: int, db_session) -> set[tuple[int, str]]:
    """(team_id, as_of_date) únicos realmente necesitados -- as_of_date es
    game_date del juego (la resta de 1 día para el corte del roster se
    aplica al pedir el roster, igual que bullpen_era_as_of). Un
    doubleheader en la misma fecha no agrega una llamada extra: mismo
    (team_id, as_of_date) para ambos juegos."""
    games = db_session.query(HistoricalGame).filter_by(run_id=run_id, season_year=season_year).all()
    pairs = set()
    for g in games:
        if g.away_team_id:
            pairs.add((g.away_team_id, g.game_date))
        if g.home_team_id:
            pairs.add((g.home_team_id, g.game_date))
    return pairs


def ingest_raw_batting_logs(season_year: int, run_id: int, session_factory=None) -> dict:
    """
    Trae el gameLog de bateo COMPLETO de temporada (una sola llamada) por
    cada team_id que aparece en HistoricalGame de esta temporada, y lo
    guarda en HistoricalRawBattingLog. Salta equipos que ya tienen filas
    (idempotente -- retomar tras un corte no re-descarga nada).
    """
    session_factory = session_factory or SessionLocal
    db_session = session_factory()
    n_teams_fetched = n_teams_skipped = n_errors = n_rows = 0
    try:
        team_ids = _team_ids_for_season(season_year, run_id, db_session)
        for team_id in sorted(team_ids):
            entity_key = str(team_id)
            if _already_fetched(db_session, "batting_team", entity_key, season_year):
                n_teams_skipped += 1
                continue

            try:
                payload = _http_get(f"{MLB_API_BASE}/teams/{team_id}/stats",
                                     {"stats": "gameLog", "group": "hitting", "season": season_year})
                splits = payload["stats"][0]["splits"]
            except (requests.RequestException, KeyError, IndexError, ValueError) as e:
                logger.warning(f"[raw_ingestion] gameLog de bateo falló para equipo {team_id} ({season_year}): {e}")
                n_errors += 1
                continue

            for split in splits:
                stat = split.get("stat", {})
                game_date = split.get("date")
                if not game_date:
                    continue
                db_session.add(HistoricalRawBattingLog(
                    team_id=team_id, season_year=season_year, game_date=game_date,
                    at_bats=stat.get("atBats"), hits=stat.get("hits"),
                    doubles=stat.get("doubles"), triples=stat.get("triples"),
                    home_runs=stat.get("homeRuns"), walks=stat.get("baseOnBalls"),
                    hit_by_pitch=stat.get("hitByPitch"), sac_flies=stat.get("sacFlies"),
                    plate_appearances=stat.get("plateAppearances"),
                ))
                n_rows += 1
            _mark_fetched(db_session, "batting_team", entity_key, season_year)
            db_session.commit()
            n_teams_fetched += 1
    finally:
        db_session.close()

    return {
        "season_year": season_year, "n_teams_fetched": n_teams_fetched,
        "n_teams_skipped_already_cached": n_teams_skipped, "n_errors": n_errors, "n_rows": n_rows,
    }


def ingest_raw_roster_snapshots(season_year: int, run_id: int, session_factory=None) -> dict:
    """
    Trae el roster de pitcheo ACTIVO (mismo endpoint y misma fecha de corte
    -1 día que point_in_time_provider.py::bullpen_era_as_of) para cada
    (team_id, as_of_date) único que HistoricalGame realmente necesita, y lo
    guarda en HistoricalRawRosterSnapshot. Salta pares ya cacheados.
    """
    session_factory = session_factory or SessionLocal
    db_session = session_factory()
    n_fetched = n_skipped = n_errors = n_rows = 0
    try:
        pairs = _distinct_team_as_of_dates(season_year, run_id, db_session)
        for team_id, as_of_date in sorted(pairs):
            entity_key = f"{team_id}:{as_of_date}"
            if _already_fetched(db_session, "roster_snapshot", entity_key, season_year):
                n_skipped += 1
                continue

            end_date = (date.fromisoformat(as_of_date) - timedelta(days=1)).strftime("%Y-%m-%d")
            try:
                payload = _http_get(f"{MLB_API_BASE}/teams/{team_id}/roster",
                                     {"rosterType": "active", "date": end_date})
                roster = payload.get("roster", [])
            except (requests.RequestException, KeyError, ValueError) as e:
                logger.debug(f"[raw_ingestion] roster falló para equipo {team_id} @ {as_of_date}: {e}")
                n_errors += 1
                continue

            pitcher_ids = [p["person"]["id"] for p in roster if p.get("position", {}).get("abbreviation") == "P"]
            for pitcher_id in pitcher_ids:
                db_session.add(HistoricalRawRosterSnapshot(
                    team_id=team_id, season_year=season_year, as_of_date=as_of_date, pitcher_id=pitcher_id,
                ))
                n_rows += 1
            _mark_fetched(db_session, "roster_snapshot", entity_key, season_year)
            db_session.commit()
            n_fetched += 1
    finally:
        db_session.close()

    return {
        "season_year": season_year, "n_snapshots_fetched": n_fetched,
        "n_snapshots_skipped_already_cached": n_skipped, "n_errors": n_errors, "n_rows": n_rows,
    }


def ingest_raw_pitching_logs(season_year: int, run_id: int, session_factory=None) -> dict:
    """
    Trae el gameLog de pitcheo COMPLETO de temporada (una sola llamada) por
    cada pitcher_id relevante: abridores en HistoricalGame UNION cualquier
    pitcher visto en HistoricalRawRosterSnapshot (bullpen). Correr DESPUÉS
    de ingest_raw_roster_snapshots -- si el roster todavía no está
    poblado, esto solo cubre abridores.
    """
    session_factory = session_factory or SessionLocal
    db_session = session_factory()
    n_fetched = n_skipped = n_errors = n_rows = 0
    try:
        pitcher_ids = _starter_pitcher_ids_for_season(season_year, run_id, db_session)
        roster_pitcher_ids = {
            row[0] for row in db_session.query(HistoricalRawRosterSnapshot.pitcher_id)
            .filter_by(season_year=season_year).distinct().all()
        }
        pitcher_ids |= roster_pitcher_ids

        for pitcher_id in sorted(pitcher_ids):
            entity_key = str(pitcher_id)
            if _already_fetched(db_session, "pitching_pitcher", entity_key, season_year):
                n_skipped += 1
                continue

            try:
                payload = _http_get(f"{MLB_API_BASE}/people/{pitcher_id}/stats",
                                     {"stats": "gameLog", "group": "pitching", "season": season_year})
                splits = payload["stats"][0]["splits"]
            except (requests.RequestException, KeyError, IndexError, ValueError) as e:
                logger.warning(f"[raw_ingestion] gameLog de pitcheo falló para pitcher {pitcher_id} ({season_year}): {e}")
                n_errors += 1
                continue

            for split in splits:
                stat = split.get("stat", {})
                game_date = split.get("date")
                if not game_date:
                    continue
                db_session.add(HistoricalRawPitchingLog(
                    pitcher_id=pitcher_id, season_year=season_year, game_date=game_date,
                    innings_pitched=_parse_innings(stat.get("inningsPitched")),
                    earned_runs=stat.get("earnedRuns"), strikeouts=stat.get("strikeOuts"),
                    walks=stat.get("baseOnBalls"), batters_faced=stat.get("battersFaced"),
                    number_of_pitches=stat.get("numberOfPitches") or stat.get("pitchesThrown"),
                    game_started=bool(stat.get("gamesStarted")),
                ))
                n_rows += 1
            _mark_fetched(db_session, "pitching_pitcher", entity_key, season_year)
            db_session.commit()
            n_fetched += 1
    finally:
        db_session.close()

    return {
        "season_year": season_year, "n_pitchers_fetched": n_fetched,
        "n_pitchers_skipped_already_cached": n_skipped, "n_errors": n_errors, "n_rows": n_rows,
    }


def ingest_raw_logs_for_season(season_year: int, run_id: int, session_factory=None) -> dict:
    """Orquesta las 3 capas en el orden correcto: bateo (independiente),
    roster (independiente), pitcheo (depende del roster para incluir
    bullpen, no solo abridores)."""
    batting = ingest_raw_batting_logs(season_year, run_id, session_factory)
    roster = ingest_raw_roster_snapshots(season_year, run_id, session_factory)
    pitching = ingest_raw_pitching_logs(season_year, run_id, session_factory)
    return {"batting": batting, "roster": roster, "pitching": pitching}
