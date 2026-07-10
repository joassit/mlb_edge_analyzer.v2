"""
Proveedor de estadísticas PUNTO-EN-EL-TIEMPO -- la pieza central de las
protecciones anti-fuga de este motor.

Por qué esto NO reutiliza data/stats.py directamente: esas funciones
(get_pitcher_era_ip, get_team_ops, get_bullpen_era, get_pitcher_command,
get_pitcher_rest en data/stats.py) piden `stats=season` (temporada
COMPLETA acumulada al momento de la llamada) y cachean por
(pitcher_id/team_id, season) -- sin fecha de corte. Usarlas para
reconstruir "cómo se veía este pitcher ANTES del 15 de abril" devolvería
sus números de la temporada ENTERA (incluyendo agosto, septiembre...),
exactamente el look-ahead bias que este módulo existe para prevenir.

Este archivo pide en cambio `stats=byDateRange` con `endDate` estrictamente
anterior a `as_of_date`, y NO comparte ninguna caché con data/stats.py (ni
siquiera el mismo diccionario en memoria) -- una corrida histórica nunca
puede leer ni escribir el estado interno del módulo de producción.
"""

import logging
from datetime import date, datetime, timedelta

import requests

from data.http import session
from historical_engine.config import MLB_API_BASE, INGESTION_REQUEST_TIMEOUT

logger = logging.getLogger("mlb_edge_analyzer.historical")

OPEN_METEO_ARCHIVE_BASE = "https://archive-api.open-meteo.com/v1/archive"


def _parse_innings(ip_str: str) -> float:
    """Mismo parseo que data/stats.py::_parse_innings -- '63.1' son 63 y
    1/3 entradas, no 63.1 en base 10. Duplicado a propósito (ver docstring
    del módulo): es una función pura sin estado, cero riesgo de
    contaminación por duplicarla, y evita que este motor dependa de un
    detalle interno de data/stats.py que podría cambiar."""
    if "." not in ip_str:
        return float(ip_str)
    whole, frac = ip_str.split(".")
    thirds = {"0": 0, "1": 1, "2": 2}.get(frac, 0)
    return int(whole) + thirds / 3


def _season_start(season: int) -> str:
    """Aproximación conservadora del inicio de temporada regular (1 de
    marzo) -- suficiente como límite inferior para `byDateRange`; pedir
    de más (spring training) no genera fuga, solo estadísticas de más
    contexto real que sí pasaron antes del corte."""
    return f"{season}-03-01"


class HistoricalStatsProvider:
    """
    Interfaz (duck-typed) que espera point_in_time_stats.py. Ver
    MLBStatsAPIProvider para la implementación real (llamadas HTTP con
    fecha de corte), y tests/test_historical_point_in_time.py::FakeProvider
    para la implementación usada en pruebas deterministas.
    """

    def pitcher_era_ip_as_of(self, pitcher_id: int, as_of_date: str, season: int) -> tuple[float, float] | None:
        raise NotImplementedError

    def team_ops_as_of(self, team_id: int, as_of_date: str, season: int) -> tuple[float, int | None] | None:
        """Devuelve (ops, plate_appearances) -- PA junto al OPS (mismo
        payload de la API, sin llamada extra) para poder aplicarle
        shrinkage real más adelante en vez de una aproximación por
        calendario (ver historical_engine/training.py)."""
        raise NotImplementedError

    def bullpen_era_as_of(self, team_id: int, as_of_date: str, season: int) -> float | None:
        raise NotImplementedError

    def pitcher_command_as_of(self, pitcher_id: int, as_of_date: str, season: int) -> dict:
        raise NotImplementedError

    def pitcher_rest_as_of(self, pitcher_id: int, as_of_date: str, season: int) -> dict:
        raise NotImplementedError

    def historical_weather(self, lat: float | None, lon: float | None, game_date: str) -> dict:
        raise NotImplementedError

    def league_averages_as_of(self, as_of_date: str, season: int) -> dict:
        raise NotImplementedError


class MLBStatsAPIProvider(HistoricalStatsProvider):
    """
    Implementación real: MLB Stats API con `stats=byDateRange` y
    `endDate` = as_of_date - 1 día (nunca incluye el propio día del
    corte, para no arriesgar contaminar con box scores parciales del
    mismo día en curso). Nunca usa `stats=season` (esa variante siempre
    trae el acumulado completo vigente al momento de la llamada, no
    acotado a una fecha pasada).
    """

    def _end_date(self, as_of_date: str) -> str:
        cutoff = date.fromisoformat(as_of_date) - timedelta(days=1)
        return cutoff.strftime("%Y-%m-%d")

    def pitcher_era_ip_as_of(self, pitcher_id, as_of_date, season):
        try:
            params = {
                "stats": "byDateRange", "group": "pitching",
                "startDate": _season_start(season), "endDate": self._end_date(as_of_date),
            }
            resp = session.get(f"{MLB_API_BASE}/people/{pitcher_id}/stats",
                                params=params, timeout=INGESTION_REQUEST_TIMEOUT)
            resp.raise_for_status()
            splits = resp.json()["stats"][0]["splits"]
            if not splits:
                return None
            stat = splits[0]["stat"]
            era = stat.get("era")
            ip_str = stat.get("inningsPitched")
            if era is None or ip_str is None:
                return None
            return float(era), _parse_innings(ip_str)
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            logger.debug(f"[historical] ERA/IP as-of falló para pitcher {pitcher_id} @ {as_of_date}: {e}")
            return None

    def team_ops_as_of(self, team_id, as_of_date, season):
        try:
            params = {
                "stats": "byDateRange", "group": "hitting",
                "startDate": _season_start(season), "endDate": self._end_date(as_of_date),
            }
            resp = session.get(f"{MLB_API_BASE}/teams/{team_id}/stats",
                                params=params, timeout=INGESTION_REQUEST_TIMEOUT)
            resp.raise_for_status()
            splits = resp.json()["stats"][0]["splits"]
            if not splits:
                return None
            stat = splits[0]["stat"]
            ops = stat.get("ops")
            if ops is None:
                return None
            pa_raw = stat.get("plateAppearances")
            pa = int(pa_raw) if pa_raw is not None else None
            return float(ops), pa
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            logger.debug(f"[historical] OPS as-of falló para equipo {team_id} @ {as_of_date}: {e}")
            return None

    def bullpen_era_as_of(self, team_id, as_of_date, season):
        # El endpoint de roster SÍ soporta un snapshot histórico real vía
        # `date=` (verificado empíricamente contra la API real: 9/9
        # comparaciones entre abril/julio/septiembre 2024 en 3 equipos
        # distintos dieron rosters DISTINTOS, con conteos consistentes con
        # la expansión real de roster de septiembre -- no es el roster
        # "actual" ignorando el parámetro). Se pide con la misma fecha de
        # corte que el resto del proveedor (`_end_date`, día anterior a
        # as_of_date) para que el roster nunca incluya un movimiento
        # (trade/call-up/DL) posterior al corte -- antes de este fix, un
        # jugador incorporado meses después del juego podía contarse en el
        # bullpen de un juego temprano de esa misma temporada.
        try:
            roster_resp = session.get(
                f"{MLB_API_BASE}/teams/{team_id}/roster",
                params={"rosterType": "active", "date": self._end_date(as_of_date)},
                timeout=INGESTION_REQUEST_TIMEOUT,
            )
            roster_resp.raise_for_status()
            roster = roster_resp.json().get("roster", [])
        except requests.RequestException as e:
            logger.debug(f"[historical] roster as-of falló para equipo {team_id} @ {as_of_date}: {e}")
            return None

        pitcher_ids = [p["person"]["id"] for p in roster if p.get("position", {}).get("abbreviation") == "P"]
        total_ip, weighted_era_sum = 0.0, 0.0
        for pid in pitcher_ids:
            result = self.pitcher_era_ip_as_of(pid, as_of_date, season)
            if result is None:
                continue
            era, ip = result
            if ip <= 0:
                continue
            weighted_era_sum += era * ip
            total_ip += ip
        return (weighted_era_sum / total_ip) if total_ip > 0 else None

    def pitcher_command_as_of(self, pitcher_id, as_of_date, season):
        result = {"k_pct": None, "bb_pct": None}
        try:
            params = {
                "stats": "byDateRange", "group": "pitching",
                "startDate": _season_start(season), "endDate": self._end_date(as_of_date),
            }
            resp = session.get(f"{MLB_API_BASE}/people/{pitcher_id}/stats",
                                params=params, timeout=INGESTION_REQUEST_TIMEOUT)
            resp.raise_for_status()
            splits = resp.json()["stats"][0]["splits"]
            if splits:
                stat = splits[0]["stat"]
                batters_faced = stat.get("battersFaced")
                k, bb = stat.get("strikeOuts"), stat.get("baseOnBalls")
                if batters_faced:
                    if k is not None:
                        result["k_pct"] = k / batters_faced
                    if bb is not None:
                        result["bb_pct"] = bb / batters_faced
        except (requests.RequestException, KeyError, IndexError, ValueError, ZeroDivisionError) as e:
            logger.debug(f"[historical] K%%/BB%% as-of falló para pitcher {pitcher_id} @ {as_of_date}: {e}")
        return result

    def pitcher_rest_as_of(self, pitcher_id, as_of_date, season):
        result = {"days_rest": None, "last_outing_pitches": None}
        try:
            params = {"stats": "gameLog", "group": "pitching", "season": season}
            resp = session.get(f"{MLB_API_BASE}/people/{pitcher_id}/stats",
                                params=params, timeout=INGESTION_REQUEST_TIMEOUT)
            resp.raise_for_status()
            splits = resp.json()["stats"][0]["splits"]
            # LA LÍNEA QUE PROTEGE CONTRA LOOK-AHEAD: solo salidas con fecha
            # ESTRICTAMENTE anterior a as_of_date -- nunca el propio día ni
            # ninguno posterior, sin importar qué traiga el gameLog crudo.
            played_before_cutoff = [s for s in splits if s.get("date", "") < as_of_date]
            if played_before_cutoff:
                splits_sorted = sorted(played_before_cutoff, key=lambda s: s.get("date", ""), reverse=True)
                last_game = splits_sorted[0]
                last_date_str = last_game.get("date")
                pitches = last_game.get("stat", {}).get("numberOfPitches") or last_game.get("stat", {}).get("pitchesThrown")
                if last_date_str:
                    last_date = date.fromisoformat(last_date_str)
                    cutoff_date = date.fromisoformat(as_of_date)
                    result["days_rest"] = (cutoff_date - last_date).days
                result["last_outing_pitches"] = pitches
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            logger.debug(f"[historical] descanso as-of falló para pitcher {pitcher_id} @ {as_of_date}: {e}")
        return result

    def historical_weather(self, lat, lon, game_date):
        # Open-Meteo ARCHIVE (no forecast) -- el único endpoint capaz de
        # devolver clima de una fecha ya pasada. data/weather.py usa el
        # endpoint de forecast a propósito porque solo le importa hoy/
        # próximos días -- no es reutilizable acá sin dar resultados
        # incorrectos para fechas viejas.
        result = {"temp_f": None}
        if lat is None or lon is None or not game_date:
            return result
        try:
            params = {
                "latitude": lat, "longitude": lon,
                "start_date": game_date, "end_date": game_date,
                "hourly": "temperature_2m", "temperature_unit": "fahrenheit", "timezone": "UTC",
            }
            resp = session.get(OPEN_METEO_ARCHIVE_BASE, params=params, timeout=INGESTION_REQUEST_TIMEOUT)
            resp.raise_for_status()
            temps = resp.json().get("hourly", {}).get("temperature_2m", [])
            if temps:
                mid = temps[len(temps) // 2]
                if mid is not None:
                    result["temp_f"] = float(mid)
        except (requests.RequestException, KeyError, IndexError, ValueError, TypeError) as e:
            logger.debug(f"[historical] clima archivado falló para {game_date}: {e}")
        return result

    def league_averages_as_of(self, as_of_date, season):
        """OPS/ERA/carreras-por-juego de liga, acotados a byDateRange antes
        del corte -- mismo rol que data/stats.py::get_league_ops()/
        get_league_era()/get_league_runs_per_game(), pero sin su caché
        (esas cachean por season completa, sin fecha) y sin depender de
        `stats=season` (acumulado completo vigente)."""
        result = {"league_ops": None, "league_era": None, "league_runs_per_game": None}
        end_date = self._end_date(as_of_date)
        start_date = _season_start(season)

        try:
            resp = session.get(
                f"{MLB_API_BASE}/stats",
                params={"stats": "byDateRange", "group": "hitting", "sportId": 1, "limit": 300,
                        "startDate": start_date, "endDate": end_date},
                timeout=INGESTION_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            weighted_sum, total_pa = 0.0, 0
            for split in resp.json()["stats"][0]["splits"]:
                stat = split["stat"]
                pa = int(stat.get("plateAppearances", 0))
                ops = stat.get("ops")
                if pa >= 100 and ops is not None:
                    weighted_sum += float(ops) * pa
                    total_pa += pa
            if total_pa > 0:
                result["league_ops"] = weighted_sum / total_pa
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            logger.debug(f"[historical] OPS de liga as-of falló @ {as_of_date}: {e}")

        try:
            resp = session.get(
                f"{MLB_API_BASE}/teams/stats",
                params={"stats": "byDateRange", "group": "pitching", "sportId": 1,
                        "startDate": start_date, "endDate": end_date},
                timeout=INGESTION_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            weighted_era_sum, total_ip = 0.0, 0.0
            for split in resp.json()["stats"][0]["splits"]:
                stat = split["stat"]
                ip_str, era = stat.get("inningsPitched"), stat.get("era")
                if ip_str is None or era is None:
                    continue
                ip = _parse_innings(str(ip_str))
                if ip <= 0:
                    continue
                weighted_era_sum += float(era) * ip
                total_ip += ip
            if total_ip > 0:
                result["league_era"] = weighted_era_sum / total_ip
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            logger.debug(f"[historical] ERA de liga as-of falló @ {as_of_date}: {e}")

        try:
            resp = session.get(
                f"{MLB_API_BASE}/teams/stats",
                params={"stats": "byDateRange", "group": "hitting", "sportId": 1,
                        "startDate": start_date, "endDate": end_date},
                timeout=INGESTION_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            total_runs, total_games = 0, 0
            for split in resp.json()["stats"][0]["splits"]:
                stat = split["stat"]
                runs, games = stat.get("runs"), stat.get("gamesPlayed")
                if runs is None or not games:
                    continue
                total_runs += int(runs)
                total_games += int(games)
            if total_games > 0:
                result["league_runs_per_game"] = total_runs / total_games
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            logger.debug(f"[historical] carreras/juego de liga as-of falló @ {as_of_date}: {e}")

        return result
