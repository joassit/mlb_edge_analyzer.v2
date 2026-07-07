"""
Estadísticas de temporada vía MLB Stats API.

Usamos la API oficial en vez de scraping de baseball-reference (más estable,
sin bloqueos por rate limit, sin depender de que pybaseball siga funcionando).
"""

import logging
import statistics
import requests

from config import MLB_API_BASE, SEASON, MIN_PA_FOR_LEAGUE_OPS, FALLBACK_BULLPEN_ERA

logger = logging.getLogger("mlb_edge_analyzer")

_pitcher_stats_cache: dict[int, dict] = {}
_league_ops_cache: float | None = None
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


def get_pitcher_era(pitcher_id: int, season: int = SEASON) -> float | None:
    """ERA de temporada regular de un pitcher específico, por su MLB ID."""
    if pitcher_id in _pitcher_stats_cache:
        return _pitcher_stats_cache[pitcher_id].get("era")

    params = {"stats": "season", "group": "pitching", "season": season}
    resp = requests.get(f"{MLB_API_BASE}/people/{pitcher_id}/stats", params=params, timeout=15)
    resp.raise_for_status()
    payload = resp.json()

    try:
        splits = payload["stats"][0]["splits"]
        if not splits:
            return None
        era = float(splits[0]["stat"]["era"])
        _pitcher_stats_cache[pitcher_id] = {"era": era}
        return era
    except (KeyError, IndexError, ValueError) as e:
        logger.warning(f"No se pudo obtener ERA del pitcher {pitcher_id}: {e}")
        return None


def get_team_ops(team_id: int, season: int = SEASON) -> float | None:
    """OPS de equipo de temporada regular (ofensiva del rival)."""
    params = {"stats": "season", "group": "hitting", "season": season}
    resp = requests.get(f"{MLB_API_BASE}/teams/{team_id}/stats", params=params, timeout=15)
    resp.raise_for_status()
    payload = resp.json()

    try:
        splits = payload["stats"][0]["splits"]
        if not splits:
            return None
        return float(splits[0]["stat"]["ops"])
    except (KeyError, IndexError, ValueError) as e:
        logger.warning(f"No se pudo obtener OPS del equipo {team_id}: {e}")
        return None


def get_league_ops(season: int = SEASON) -> float:
    """
    Promedio de OPS de liga entre bateadores calificados (con un mínimo de
    turnos al bate), para usar como referencia neutral cuando no queremos
    comparar contra un equipo rival específico.
    """
    global _league_ops_cache
    if _league_ops_cache is not None:
        return _league_ops_cache

    params = {
        "stats": "season",
        "group": "hitting",
        "season": season,
        "sportId": 1,
        "limit": 300,
    }
    resp = requests.get(f"{MLB_API_BASE}/stats", params=params, timeout=15)
    resp.raise_for_status()
    payload = resp.json()

    ops_values = []
    for split in payload["stats"][0]["splits"]:
        stat = split["stat"]
        pa = int(stat.get("plateAppearances", 0))
        ops = stat.get("ops")
        if pa >= MIN_PA_FOR_LEAGUE_OPS and ops is not None:
            ops_values.append(float(ops))

    _league_ops_cache = statistics.mean(ops_values) if ops_values else 0.750
    return _league_ops_cache


def get_bullpen_era(team_id: int, season: int = SEASON) -> float:
    """
    ERA del bullpen de un equipo, calculado como el promedio ponderado por
    entradas lanzadas de todos los relevistas del roster activo.

    Un jugador se clasifica como 'relevista' si tiene 0 aperturas, o si
    empezó menos del 50% de los juegos en los que apareció (long relievers
    que a veces abren).
    """
    cache_key = f"{team_id}-{season}"
    if cache_key in _bullpen_cache:
        return _bullpen_cache[cache_key]

    try:
        roster_resp = requests.get(
            f"{MLB_API_BASE}/teams/{team_id}/roster",
            params={"rosterType": "active", "season": season},
            timeout=15,
        )
        roster_resp.raise_for_status()
        roster = roster_resp.json().get("roster", [])
    except requests.RequestException as e:
        logger.warning(f"No se pudo obtener roster del equipo {team_id}, usando fallback: {e}")
        _bullpen_cache[cache_key] = FALLBACK_BULLPEN_ERA
        return FALLBACK_BULLPEN_ERA

    pitcher_ids = [
        p["person"]["id"] for p in roster
        if p.get("position", {}).get("abbreviation") == "P"
    ]

    total_ip = 0.0
    weighted_era_sum = 0.0

    for pid in pitcher_ids:
        try:
            resp = requests.get(
                f"{MLB_API_BASE}/people/{pid}/stats",
                params={"stats": "season", "group": "pitching", "season": season},
                timeout=15,
            )
            resp.raise_for_status()
            splits = resp.json()["stats"][0]["splits"]
            if not splits:
                continue

            stat = splits[0]["stat"]
            games = int(stat.get("gamesPlayed", 0))
            starts = int(stat.get("gamesStarted", 0))
            era = stat.get("era")
            ip_str = stat.get("inningsPitched")

            if era is None or ip_str is None or games == 0:
                continue

            is_reliever = starts == 0 or (starts / games) < 0.5
            if not is_reliever:
                continue

            ip = _parse_innings(ip_str)
            if ip <= 0:
                continue

            weighted_era_sum += float(era) * ip
            total_ip += ip

        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            logger.debug(f"Se omitió pitcher {pid} en cálculo de bullpen: {e}")
            continue

    bullpen_era = (weighted_era_sum / total_ip) if total_ip > 0 else FALLBACK_BULLPEN_ERA
    _bullpen_cache[cache_key] = bullpen_era
    return bullpen_era


def get_pitcher_command(pitcher_id: int, season: int = SEASON) -> dict:
    """
    K% y BB% del abridor (strikeOuts / bateadores enfrentados,
    baseOnBalls / bateadores enfrentados). Devuelve también whip por si acaso.
    """
    cache_key = f"cmd-{pitcher_id}-{season}"
    if cache_key in _pitcher_stats_cache:
        return _pitcher_stats_cache[cache_key]

    result = {"k_pct": None, "bb_pct": None, "whip": None}
    try:
        params = {"stats": "season", "group": "pitching", "season": season}
        resp = requests.get(f"{MLB_API_BASE}/people/{pitcher_id}/stats", params=params, timeout=15)
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

    _pitcher_stats_cache[cache_key] = result
    return result


def get_pitcher_rest(pitcher_id: int, season: int = SEASON) -> dict:
    """
    Días de descanso desde la última salida y pitches lanzados en esa salida.
    Se basa en el game log más reciente del pitcher.
    """
    from datetime import date as _date

    cache_key = f"rest-{pitcher_id}-{season}"
    if cache_key in _pitcher_stats_cache:
        return _pitcher_stats_cache[cache_key]

    result = {"days_rest": None, "last_outing_pitches": None}
    try:
        params = {"stats": "gameLog", "group": "pitching", "season": season}
        resp = requests.get(f"{MLB_API_BASE}/people/{pitcher_id}/stats", params=params, timeout=15)
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

    _pitcher_stats_cache[cache_key] = result
    return result


def get_team_batting_advanced(team_id: int, season: int = SEASON) -> dict:
    """BABIP e ISO del equipo, calculados a partir de stats crudos (sin depender de FanGraphs)."""
    result = {"babip": None, "iso": None}
    try:
        params = {"stats": "season", "group": "hitting", "season": season}
        resp = requests.get(f"{MLB_API_BASE}/teams/{team_id}/stats", params=params, timeout=15)
        resp.raise_for_status()
        splits = resp.json()["stats"][0]["splits"]
        if not splits:
            return result

        stat = splits[0]["stat"]
        hits = stat.get("hits")
        hr = stat.get("homeRuns")
        ab = stat.get("atBats")
        so = stat.get("strikeOuts")
        sf = stat.get("sacFlies", 0) or 0
        avg = stat.get("avg")
        slg = stat.get("slg")

        if None not in (hits, hr, ab, so) and (ab - so - hr + sf) > 0:
            result["babip"] = (hits - hr) / (ab - so - hr + sf)
        if avg is not None and slg is not None:
            result["iso"] = float(slg) - float(avg)
    except (requests.RequestException, KeyError, IndexError, ValueError, TypeError) as e:
        logger.warning(f"No se pudo obtener BABIP/ISO del equipo {team_id}: {e}")

    return result


if __name__ == "__main__":
    print("OPS de liga:", round(get_league_ops(), 3))
