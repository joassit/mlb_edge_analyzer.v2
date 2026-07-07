"""
Estadísticas de temporada vía MLB Stats API.

Usamos la API oficial en vez de scraping de baseball-reference (más estable,
sin bloqueos por rate limit, sin depender de que pybaseball siga funcionando).
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
import requests

from config import MLB_API_BASE, SEASON, MIN_PA_FOR_LEAGUE_OPS, FALLBACK_BULLPEN_ERA
from data.http import session

logger = logging.getLogger("mlb_edge_analyzer")

# Protege las tres cachés de abajo (todas comparten este único lock): sin
# esto, dos threads que llaman get_league_ops()/get_pitcher_era_ip()/etc. al
# mismo tiempo pueden leer "cache vacía" ambos, disparar dos llamadas a la
# API en paralelo y pisarse la escritura -- inofensivo en la práctica porque
# el GIL evita un crash, pero el resultado queda no-determinístico según
# quién escriba último.
_cache_lock = threading.Lock()

_pitcher_stats_cache: dict[int, dict] = {}
_league_ops_cache: float | None = None
_league_era_cache: float | None = None
_league_runs_per_game_cache: float | None = None
_bullpen_cache: dict[int, float] = {}


def _parse_innings(ip_str: str) -> float:
    """
    MLB reporta entradas lanzadas en formato '63.1' o '63.2', donde el
    decimal NO es base 10: .1 = un tercio de entrada, .2 = dos tercios.
    '63.1' son en realidad 63 y 1/3 entradas, no 63.1 entradas.
    """
    if "." not in ip_str:
        return float(ip_str)
    whole, frac = ip_str.split(".")
    thirds = {"0": 0, "1": 1, "2": 2}.get(frac, 0)
    return int(whole) + thirds / 3


def get_pitcher_era_ip(pitcher_id: int, season: int = SEASON) -> tuple[float, float] | None:
    """
    ERA e innings pitched (ya parseados a decimal real, no el formato
    '63.1'/'63.2' de la API) de un pitcher. Usado para aplicar shrinkage
    hacia el promedio de liga en muestras chicas -- ver model/adjustments.py.
    None si no hay datos (red, timeout, esquema) -- nunca propaga la
    excepción hacia el pipeline.
    """
    cache_key = f"era-ip-{pitcher_id}-{season}"
    with _cache_lock:
        if cache_key in _pitcher_stats_cache:
            cached = _pitcher_stats_cache[cache_key]
            return (cached["era"], cached["ip"]) if cached else None

    try:
        params = {"stats": "season", "group": "pitching", "season": season}
        resp = session.get(f"{MLB_API_BASE}/people/{pitcher_id}/stats", params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        splits = payload["stats"][0]["splits"]
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
        logger.warning(f"No se pudo obtener ERA/IP del pitcher {pitcher_id}: {e}")
        with _cache_lock:
            _pitcher_stats_cache[cache_key] = None
        return None


def get_team_ops(team_id: int, season: int = SEASON) -> float | None:
    """OPS de equipo de temporada regular (ofensiva del rival). None si la
    API falla -- nunca propaga la excepción."""
    try:
        params = {"stats": "season", "group": "hitting", "season": season}
        resp = session.get(f"{MLB_API_BASE}/teams/{team_id}/stats", params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        splits = payload["stats"][0]["splits"]
        if not splits:
            return None
        return float(splits[0]["stat"]["ops"])
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.warning(f"No se pudo obtener OPS del equipo {team_id}: {e}")
        return None


def get_league_ops(season: int = SEASON) -> float:
    """
    OPS promedio de liga entre bateadores calificados (con un mínimo de
    turnos al bate), ponderado por plate appearances -- no un promedio
    simple entre jugadores. Sin ponderar, un bateador con 105 PA pesa igual
    que uno con 600, distorsionando el promedio hacia los jugadores de
    muestra chica que apenas califican.
    """
    global _league_ops_cache
    with _cache_lock:
        if _league_ops_cache is not None:
            return _league_ops_cache

    try:
        params = {
            "stats": "season",
            "group": "hitting",
            "season": season,
            "sportId": 1,
            "limit": 300,
        }
        resp = session.get(f"{MLB_API_BASE}/stats", params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()

        weighted_ops_sum = 0.0
        total_pa = 0
        for split in payload["stats"][0]["splits"]:
            stat = split["stat"]
            pa = int(stat.get("plateAppearances", 0))
            ops = stat.get("ops")
            if pa >= MIN_PA_FOR_LEAGUE_OPS and ops is not None:
                weighted_ops_sum += float(ops) * pa
                total_pa += pa

        result = (weighted_ops_sum / total_pa) if total_pa > 0 else 0.750
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.warning(f"No se pudo obtener OPS de liga, usando fallback 0.750: {e}")
        result = 0.750

    with _cache_lock:
        _league_ops_cache = result
    return _league_ops_cache


def get_league_era(season: int = SEASON) -> float:
    """
    ERA promedio de liga, ponderado por entradas lanzadas de cada equipo
    (mismo criterio de ponderación que get_league_ops() pondera por PA) --
    A2: antes model.runs_projection.LEAGUE_AVG_ERA era una constante fija
    (4.30) mientras el OPS de liga sí se traía en vivo, un sesgo
    direccional si el entorno de carreras real de la temporada se aleja de
    ese valor. Cae a LEAGUE_AVG_ERA (ya no como valor fijo, solo como
    fallback) si la API falla.
    """
    global _league_era_cache
    with _cache_lock:
        if _league_era_cache is not None:
            return _league_era_cache

    from model.runs_projection import LEAGUE_AVG_ERA
    try:
        params = {"stats": "season", "group": "pitching", "season": season, "sportId": 1}
        resp = session.get(f"{MLB_API_BASE}/teams/stats", params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()

        weighted_era_sum = 0.0
        total_ip = 0.0
        for split in payload["stats"][0]["splits"]:
            stat = split["stat"]
            ip_str = stat.get("inningsPitched")
            era = stat.get("era")
            if ip_str is None or era is None:
                continue
            ip = _parse_innings(str(ip_str))
            if ip <= 0:
                continue
            weighted_era_sum += float(era) * ip
            total_ip += ip

        result = (weighted_era_sum / total_ip) if total_ip > 0 else LEAGUE_AVG_ERA
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.warning(f"No se pudo obtener ERA de liga, usando fallback {LEAGUE_AVG_ERA}: {e}")
        result = LEAGUE_AVG_ERA

    with _cache_lock:
        _league_era_cache = result
    return _league_era_cache


def get_league_runs_per_game(season: int = SEASON) -> float:
    """
    Carreras anotadas promedio POR EQUIPO por juego (no el total combinado
    de un juego) -- sum(runs de todos los equipos) / sum(juegos jugados de
    todos los equipos), consistente con cómo model.runs_projection.
    project_team_runs() usa LEAGUE_AVG_RUNS_PER_GAME (multiplicado por el
    offense_factor de UN equipo, no de dos). Cae a LEAGUE_AVG_RUNS_PER_GAME
    (ya no como valor fijo, solo como fallback) si la API falla.
    """
    global _league_runs_per_game_cache
    with _cache_lock:
        if _league_runs_per_game_cache is not None:
            return _league_runs_per_game_cache

    from model.runs_projection import LEAGUE_AVG_RUNS_PER_GAME
    try:
        params = {"stats": "season", "group": "hitting", "season": season, "sportId": 1}
        resp = session.get(f"{MLB_API_BASE}/teams/stats", params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()

        total_runs = 0
        total_games = 0
        for split in payload["stats"][0]["splits"]:
            stat = split["stat"]
            runs = stat.get("runs")
            games = stat.get("gamesPlayed")
            if runs is None or not games:
                continue
            total_runs += int(runs)
            total_games += int(games)

        result = (total_runs / total_games) if total_games > 0 else LEAGUE_AVG_RUNS_PER_GAME
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.warning(f"No se pudo obtener carreras/juego de liga, usando fallback {LEAGUE_AVG_RUNS_PER_GAME}: {e}")
        result = LEAGUE_AVG_RUNS_PER_GAME

    with _cache_lock:
        _league_runs_per_game_cache = result
    return _league_runs_per_game_cache


def _fetch_reliever_era_ip(pid: int, season: int) -> tuple[float, float] | None:
    """
    ERA/IP de UN pitcher si califica como relevista, o None si no aplica
    (no es relevista, faltan datos, o la llamada falla) -- separado de
    get_bullpen_era() para poder llamarlo en paralelo (ThreadPoolExecutor)
    en vez de secuencial, un pitcher a la vez.
    """
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
        era = stat.get("era")
        ip_str = stat.get("inningsPitched")

        if era is None or ip_str is None or games == 0:
            return None

        is_reliever = starts == 0 or (starts / games) < 0.5
        if not is_reliever:
            return None

        ip = _parse_innings(ip_str)
        if ip <= 0:
            return None

        return float(era), ip
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.debug(f"Se omitió pitcher {pid} en cálculo de bullpen: {e}")
        return None


def get_bullpen_era(team_id: int, season: int = SEASON) -> float:
    """
    ERA del bullpen de un equipo, calculado como el promedio ponderado por
    entradas lanzadas de todos los relevistas del roster activo.

    Un jugador se clasifica como 'relevista' si tiene 0 aperturas, o si
    empezó menos del 50% de los juegos en los que apareció (long relievers
    que a veces abren).

    Las ~12-15 llamadas por pitcher del roster se hacen EN PARALELO
    (ThreadPoolExecutor, mismo patrón que data/weather.py::preload_weather())
    en vez de secuenciales -- antes, un timeout de 15s en un solo pitcher
    bloqueaba a los siguientes uno por uno.
    """
    cache_key = f"{team_id}-{season}"
    with _cache_lock:
        if cache_key in _bullpen_cache:
            return _bullpen_cache[cache_key]

    try:
        roster_resp = session.get(
            f"{MLB_API_BASE}/teams/{team_id}/roster",
            params={"rosterType": "active", "season": season},
            timeout=15,
        )
        roster_resp.raise_for_status()
        roster = roster_resp.json().get("roster", [])
    except requests.RequestException as e:
        logger.warning(f"No se pudo obtener roster del equipo {team_id}, usando fallback: {e}")
        with _cache_lock:
            _bullpen_cache[cache_key] = FALLBACK_BULLPEN_ERA
        return FALLBACK_BULLPEN_ERA

    pitcher_ids = [
        p["person"]["id"] for p in roster
        if p.get("position", {}).get("abbreviation") == "P"
    ]

    total_ip = 0.0
    weighted_era_sum = 0.0

    with ThreadPoolExecutor(max_workers=8) as executor:
        for result in executor.map(lambda pid: _fetch_reliever_era_ip(pid, season), pitcher_ids):
            if result is None:
                continue
            era, ip = result
            weighted_era_sum += era * ip
            total_ip += ip

    bullpen_era = (weighted_era_sum / total_ip) if total_ip > 0 else FALLBACK_BULLPEN_ERA
    with _cache_lock:
        _bullpen_cache[cache_key] = bullpen_era
    return bullpen_era


def get_pitcher_command(pitcher_id: int, season: int = SEASON) -> dict:
    """
    K% y BB% del abridor (strikeOuts / bateadores enfrentados,
    baseOnBalls / bateadores enfrentados). Devuelve también whip por si acaso.
    """
    cache_key = f"cmd-{pitcher_id}-{season}"
    with _cache_lock:
        if cache_key in _pitcher_stats_cache:
            return _pitcher_stats_cache[cache_key]

    result = {"k_pct": None, "bb_pct": None, "whip": None}
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
                if k is not None:
                    result["k_pct"] = k / batters_faced
                if bb is not None:
                    result["bb_pct"] = bb / batters_faced
            result["whip"] = stat.get("whip")
    except (requests.RequestException, KeyError, IndexError, ValueError, ZeroDivisionError) as e:
        logger.warning(f"No se pudo obtener K%/BB% del pitcher {pitcher_id}: {e}")

    with _cache_lock:
        _pitcher_stats_cache[cache_key] = result
    return result


def get_pitcher_rest(pitcher_id: int, season: int = SEASON) -> dict:
    """
    Días de descanso desde la última salida y pitches lanzados en esa salida.
    Se basa en el game log más reciente del pitcher.
    """
    from datetime import date as _date

    cache_key = f"rest-{pitcher_id}-{season}"
    with _cache_lock:
        if cache_key in _pitcher_stats_cache:
            return _pitcher_stats_cache[cache_key]

    result = {"days_rest": None, "last_outing_pitches": None}
    try:
        params = {"stats": "gameLog", "group": "pitching", "season": season}
        resp = session.get(f"{MLB_API_BASE}/people/{pitcher_id}/stats", params=params, timeout=15)
        resp.raise_for_status()
        splits = resp.json()["stats"][0]["splits"]
        if splits:
            # El gameLog puede incluir el juego de HOY (todavía no jugado) como
            # un registro placeholder — hay que excluirlo, si no, el "descanso"
            # sale en 0 días para pitchers que ni siquiera han salido a lanzar.
            today_str = _date.today().isoformat()
            played_splits = [s for s in splits if s.get("date", "") < today_str]

            if played_splits:
                splits_sorted = sorted(played_splits, key=lambda s: s.get("date", ""), reverse=True)
                last_game = splits_sorted[0]
                last_date_str = last_game.get("date")
                pitches = last_game.get("stat", {}).get("numberOfPitches") or last_game.get("stat", {}).get("pitchesThrown")

                if last_date_str:
                    last_date = _date.fromisoformat(last_date_str)
                    result["days_rest"] = (_date.today() - last_date).days
                result["last_outing_pitches"] = pitches
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.warning(f"No se pudo obtener descanso del pitcher {pitcher_id}: {e}")

    with _cache_lock:
        _pitcher_stats_cache[cache_key] = result
    return result


if __name__ == "__main__":
    print("OPS de liga:", round(get_league_ops(), 3))