"""Estadisticas de temporada via MLB Stats API.

Nota de fidelidad al spec: `GameSnapshot` (Seccion 3.1) pide xERA/xFIP,
metricas de Statcast/"expected stats" que la MLB Stats API no expone
directamente (requieren una fuente Statcast separada, no wireada en esta
entrega -- ver `jsa/docs/ROADMAP.md`). Mientras tanto, `get_pitcher_era_ip`
devuelve ERA real como proxy explicito: es la mejor senal disponible hoy,
no una xERA real, y el Feature Registry (`registries/seed.py`) documenta
esta limitacion en vez de esconderla.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

from jsa.config import FALLBACK_BULLPEN_ERA, MIN_PA_FOR_LEAGUE_OPS, MLB_API_BASE
from jsa.data_sources.http import session

logger = logging.getLogger("jsa")

_cache_lock = threading.Lock()
_pitcher_stats_cache: dict[str, dict | None] = {}
_league_ops_cache: float | None = None
_league_era_cache: float | None = None
_league_runs_per_game_cache: float | None = None
_bullpen_cache: dict[str, float] = {}


def _parse_innings(ip_str: str) -> float:
    """MLB reporta entradas lanzadas en formato '63.1'/'63.2', donde el
    decimal NO es base 10: .1 = un tercio de entrada, .2 = dos tercios."""
    if "." not in ip_str:
        return float(ip_str)
    whole, frac = ip_str.split(".")
    thirds = {"0": 0, "1": 1, "2": 2}.get(frac, 0)
    return int(whole) + thirds / 3


def get_pitcher_era_ip(pitcher_id: int, season: int) -> tuple[float, float] | None:
    """(ERA-como-proxy-de-xERA, IP) de un abridor esta temporada. None si no
    hay datos -- nunca propaga la excepcion."""
    cache_key = f"era-ip-{pitcher_id}-{season}"
    with _cache_lock:
        if cache_key in _pitcher_stats_cache:
            cached = _pitcher_stats_cache[cache_key]
            return (cached["era"], cached["ip"]) if cached else None

    try:
        params = {"stats": "season", "group": "pitching", "season": season}
        resp = session.get(f"{MLB_API_BASE}/people/{pitcher_id}/stats", params=params, timeout=15)
        resp.raise_for_status()
        splits = resp.json()["stats"][0]["splits"]
        if not splits:
            with _cache_lock:
                _pitcher_stats_cache[cache_key] = None
            return None
        stat = splits[0]["stat"]
        era = float(stat["era"])
        ip = _parse_innings(stat["inningsPitched"])
        with _cache_lock:
            _pitcher_stats_cache[cache_key] = {"era": era, "ip": ip}
        return era, ip
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.warning("No se pudo obtener ERA/IP del pitcher %s: %s", pitcher_id, e)
        with _cache_lock:
            _pitcher_stats_cache[cache_key] = None
        return None


def get_pitcher_command(pitcher_id: int, season: int) -> dict:
    """K%-BB% (proxy de `k_bb_pct` en GameSnapshot) y proyeccion de IP por
    salida (season IP / games started), usada como proxy de
    `starter_projected_ip`."""
    cache_key = f"cmd-{pitcher_id}-{season}"
    with _cache_lock:
        if cache_key in _pitcher_stats_cache:
            return _pitcher_stats_cache[cache_key] or {}

    result: dict = {"k_bb_pct": None, "projected_ip": None, "ip_sample": None}
    try:
        params = {"stats": "season", "group": "pitching", "season": season}
        resp = session.get(f"{MLB_API_BASE}/people/{pitcher_id}/stats", params=params, timeout=15)
        resp.raise_for_status()
        splits = resp.json()["stats"][0]["splits"]
        if splits:
            stat = splits[0]["stat"]
            batters_faced = stat.get("battersFaced")
            k = stat.get("strikeOuts")
            bb = stat.get("baseOnBalls")
            if batters_faced:
                k_pct = (k / batters_faced) if k is not None else None
                bb_pct = (bb / batters_faced) if bb is not None else None
                if k_pct is not None and bb_pct is not None:
                    result["k_bb_pct"] = k_pct - bb_pct
            ip_str = stat.get("inningsPitched")
            starts = stat.get("gamesStarted") or 0
            if ip_str is not None:
                ip = _parse_innings(str(ip_str))
                result["ip_sample"] = ip
                if starts > 0:
                    result["projected_ip"] = ip / starts
    except (requests.RequestException, KeyError, IndexError, ValueError, ZeroDivisionError) as e:
        logger.warning("No se pudo obtener K-BB%%/IP proyectada del pitcher %s: %s", pitcher_id, e)

    with _cache_lock:
        _pitcher_stats_cache[cache_key] = result
    return result


def get_team_ops(team_id: int, season: int) -> float | None:
    try:
        params = {"stats": "season", "group": "hitting", "season": season}
        resp = session.get(f"{MLB_API_BASE}/teams/{team_id}/stats", params=params, timeout=15)
        resp.raise_for_status()
        splits = resp.json()["stats"][0]["splits"]
        if not splits:
            return None
        return float(splits[0]["stat"]["ops"])
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.warning("No se pudo obtener OPS del equipo %s: %s", team_id, e)
        return None


def get_team_ops_pa_sample(team_id: int, season: int) -> int | None:
    try:
        params = {"stats": "season", "group": "hitting", "season": season}
        resp = session.get(f"{MLB_API_BASE}/teams/{team_id}/stats", params=params, timeout=15)
        resp.raise_for_status()
        splits = resp.json()["stats"][0]["splits"]
        if not splits:
            return None
        pa = splits[0]["stat"].get("plateAppearances")
        return int(pa) if pa is not None else None
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.warning("No se pudo obtener PA del equipo %s: %s", team_id, e)
        return None


def get_league_ops(season: int) -> float:
    """OPS promedio de liga, ponderado por PA (no promedio simple entre
    jugadores)."""
    global _league_ops_cache
    with _cache_lock:
        if _league_ops_cache is not None:
            return _league_ops_cache

    try:
        params = {"stats": "season", "group": "hitting", "season": season, "sportId": 1, "limit": 300}
        resp = session.get(f"{MLB_API_BASE}/stats", params=params, timeout=15)
        resp.raise_for_status()
        weighted_sum, total_pa = 0.0, 0
        for split in resp.json()["stats"][0]["splits"]:
            stat = split["stat"]
            pa = int(stat.get("plateAppearances", 0))
            ops = stat.get("ops")
            if pa >= MIN_PA_FOR_LEAGUE_OPS and ops is not None:
                weighted_sum += float(ops) * pa
                total_pa += pa
        result = (weighted_sum / total_pa) if total_pa > 0 else 0.750
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.warning("No se pudo obtener OPS de liga, usando fallback 0.750: %s", e)
        result = 0.750

    with _cache_lock:
        _league_ops_cache = result
    return _league_ops_cache


def get_league_era(season: int) -> float:
    from jsa.config import LEAGUE_AVG_ERA

    global _league_era_cache
    with _cache_lock:
        if _league_era_cache is not None:
            return _league_era_cache

    try:
        params = {"stats": "season", "group": "pitching", "season": season, "sportId": 1}
        resp = session.get(f"{MLB_API_BASE}/teams/stats", params=params, timeout=15)
        resp.raise_for_status()
        weighted_sum, total_ip = 0.0, 0.0
        for split in resp.json()["stats"][0]["splits"]:
            stat = split["stat"]
            ip_str, era = stat.get("inningsPitched"), stat.get("era")
            if ip_str is None or era is None:
                continue
            ip = _parse_innings(str(ip_str))
            if ip <= 0:
                continue
            weighted_sum += float(era) * ip
            total_ip += ip
        result = (weighted_sum / total_ip) if total_ip > 0 else LEAGUE_AVG_ERA
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.warning("No se pudo obtener ERA de liga, usando fallback %s: %s", LEAGUE_AVG_ERA, e)
        result = LEAGUE_AVG_ERA

    with _cache_lock:
        _league_era_cache = result
    return _league_era_cache


def get_league_runs_per_game(season: int) -> float:
    from jsa.config import LEAGUE_AVG_RUNS_PER_GAME

    global _league_runs_per_game_cache
    with _cache_lock:
        if _league_runs_per_game_cache is not None:
            return _league_runs_per_game_cache

    try:
        params = {"stats": "season", "group": "hitting", "season": season, "sportId": 1}
        resp = session.get(f"{MLB_API_BASE}/teams/stats", params=params, timeout=15)
        resp.raise_for_status()
        total_runs, total_games = 0, 0
        for split in resp.json()["stats"][0]["splits"]:
            stat = split["stat"]
            runs, games = stat.get("runs"), stat.get("gamesPlayed")
            if runs is None or not games:
                continue
            total_runs += int(runs)
            total_games += int(games)
        result = (total_runs / total_games) if total_games > 0 else LEAGUE_AVG_RUNS_PER_GAME
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.warning("No se pudo obtener carreras/juego de liga, usando fallback %s: %s", LEAGUE_AVG_RUNS_PER_GAME, e)
        result = LEAGUE_AVG_RUNS_PER_GAME

    with _cache_lock:
        _league_runs_per_game_cache = result
    return _league_runs_per_game_cache


def _fetch_reliever_era_ip(pid: int, season: int) -> tuple[float, float] | None:
    try:
        resp = session.get(
            f"{MLB_API_BASE}/people/{pid}/stats",
            params={"stats": "season", "group": "pitching", "season": season},
            timeout=15,
        )
        resp.raise_for_status()
        splits = resp.json()["stats"][0]["splits"]
        if not splits:
            return None
        stat = splits[0]["stat"]
        games = int(stat.get("gamesPlayed", 0))
        starts = int(stat.get("gamesStarted", 0))
        era, ip_str = stat.get("era"), stat.get("inningsPitched")
        if era is None or ip_str is None or games == 0:
            return None
        is_reliever = starts == 0 or (starts / games) < 0.5
        if not is_reliever:
            return None
        ip = _parse_innings(ip_str)
        return (float(era), ip) if ip > 0 else None
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.debug("Se omitio pitcher %s en calculo de bullpen: %s", pid, e)
        return None


def get_bullpen_era(team_id: int, season: int) -> float:
    """ERA del bullpen (promedio ponderado por IP de todos los relevistas
    del roster activo). Fetches en PARALELO -- un timeout de 15s en un solo
    pitcher ya no bloquea a los demas (leccion de
    mlb_edge_analyzer.v2/data/stats.py::get_bullpen_era)."""
    cache_key = f"{team_id}-{season}"
    with _cache_lock:
        if cache_key in _bullpen_cache:
            return _bullpen_cache[cache_key]

    try:
        roster_resp = session.get(
            f"{MLB_API_BASE}/teams/{team_id}/roster", params={"rosterType": "active", "season": season}, timeout=15
        )
        roster_resp.raise_for_status()
        roster = roster_resp.json().get("roster", [])
    except requests.RequestException as e:
        logger.warning("No se pudo obtener roster del equipo %s, usando fallback: %s", team_id, e)
        with _cache_lock:
            _bullpen_cache[cache_key] = FALLBACK_BULLPEN_ERA
        return FALLBACK_BULLPEN_ERA

    pitcher_ids = [p["person"]["id"] for p in roster if p.get("position", {}).get("abbreviation") == "P"]

    total_ip, weighted_sum = 0.0, 0.0
    with ThreadPoolExecutor(max_workers=8) as executor:
        for result in executor.map(lambda pid: _fetch_reliever_era_ip(pid, season), pitcher_ids):
            if result is None:
                continue
            era, ip = result
            weighted_sum += era * ip
            total_ip += ip

    bullpen_era = (weighted_sum / total_ip) if total_ip > 0 else FALLBACK_BULLPEN_ERA
    with _cache_lock:
        _bullpen_cache[cache_key] = bullpen_era
    return bullpen_era
