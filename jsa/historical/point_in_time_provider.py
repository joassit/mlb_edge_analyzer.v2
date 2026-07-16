"""Proveedor de estadisticas PUNTO-EN-EL-TIEMPO -- la pieza central de las
protecciones anti-fuga de este motor. Reescrito sobre la sesion HTTP
propia de JSA (`jsa/data_sources/http.py`), CERO imports de
`mlb_edge_analyzer.v2` -- mismo patron que ya demostro funcionar en
produccion real (`historical_engine/point_in_time_provider.py`), portado
por su diseño, no por su codigo.

Por que esto NO reutiliza `jsa/data_sources/stats.py`: esas funciones
piden `stats=season` (temporada COMPLETA acumulada al momento de la
llamada) y cachean por (id, season) sin fecha de corte. Reconstruir "como
se veia este pitcher ANTES del 15 de abril" con esas funciones devolveria
sus numeros de la temporada ENTERA -- el look-ahead bias exacto que este
modulo existe para prevenir (Principio 6 del spec: ningun modulo puede
usar informacion futura).

Este archivo pide en cambio `stats=byDateRange` con `endDate`
estrictamente anterior a `as_of_date`, y no comparte ninguna cache con
`data_sources/stats.py`."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import requests

from jsa.data_sources.http import session
from jsa.historical.config import INGESTION_REQUEST_TIMEOUT, MLB_API_BASE

logger = logging.getLogger("jsa.historical")

OPEN_METEO_ARCHIVE_BASE = "https://archive-api.open-meteo.com/v1/archive"

# Ventana de climatologia -- mismo criterio y mismos numeros que
# `historical_engine/point_in_time_provider.py` (con la misma leccion
# real detras: 5 años de climatologia empujo una temporada grande por
# encima del limite de 6h de GitHub Actions; 3 años recorta el costo de
# clima ~40% sin perder validez como muestra climatologica).
WEATHER_WINDOW_DAYS = 5
WEATHER_CLIMATOLOGY_YEARS = 3


def _parse_innings(ip_str: str) -> float:
    """'63.1'/'63.2' son 63 y 1/3 / 2/3 entradas, no base 10."""
    if "." not in ip_str:
        return float(ip_str)
    whole, frac = ip_str.split(".")
    thirds = {"0": 0, "1": 1, "2": 2}.get(frac, 0)
    return int(whole) + thirds / 3


def _parse_era(era: object) -> float | None:
    """MLB Stats API a veces devuelve `"-.--"` como ERA cuando es
    indefinido (ej. un pitcher con 0 entradas lanzadas pero una carrera
    cargada) -- un placeholder no numerico que `float()` rechaza. Nunca
    debe tumbar la reconstruccion de un juego entero (real: temporada
    2022, 1 de 2740 juegos, ver ROADMAP) -- se trata igual que "sin
    dato", no como un error de red."""
    try:
        return float(era)
    except (TypeError, ValueError):
        return None


def _season_start(season: int) -> str:
    return f"{season}-03-01"


class HistoricalStatsProvider:
    """Interfaz (duck-typed) -- ver `MLBStatsAPIProvider` para la
    implementacion real, y `tests/test_historical_point_in_time.py::FakeProvider`
    para la implementacion determinista usada en tests."""

    def pitcher_era_ip_as_of(self, pitcher_id: int, as_of_date: str, season: int) -> dict | None:
        """{"era": float, "ip": float, "projected_ip": float|None} -- `ip`
        es la muestra acumulada point-in-time (para shrinkage), `projected_ip`
        es IP por salida (ip / games started, mismo proxy que
        `data_sources/stats.py::get_pitcher_command()` en produccion) --
        alimenta `long_outing`/`short_outing_bullpen_game` en el Context
        Detector (Seccion 5), que a su vez mueve pesos reales via el Rule
        Engine (Seccion 6.3). `None` si no hay datos."""
        raise NotImplementedError

    def team_ops_as_of(self, team_id: int, as_of_date: str, season: int) -> tuple[float, int | None] | None:
        raise NotImplementedError

    def team_fielding_pct_as_of(self, team_id: int, as_of_date: str, season: int) -> float | None:
        """Fielding percentage de equipo, acumulado de temporada
        point-in-time -- mismo patron que `team_ops_as_of()`. Alimenta la
        senal defensiva de `team_quality` (Seccion team_quality). `None`
        si no hay datos."""
        raise NotImplementedError

    def bullpen_era_as_of(self, team_id: int, as_of_date: str, season: int) -> dict | None:
        """{"era": float|None, "closer_pitcher_id": int|None, "ip": float}
        -- el cerrador se identifica DENTRO del mismo loop roster+pitcher
        que ya calcula el ERA de bullpen (Seccion "closer_available", ver
        `injuries.py`), nunca con una llamada de red separada. `ip` es la
        MISMA suma acumulada que ya se calculaba para el promedio
        ponderado del ERA, simplemente expuesta -- permite aplicar
        `shrunk_era()` (el mismo shrinkage bayesiano de `starter`) al ERA
        de bullpen, cero trafico adicional."""
        raise NotImplementedError

    def pitcher_command_as_of(self, pitcher_id: int, as_of_date: str, season: int) -> dict:
        raise NotImplementedError

    def historical_weather(self, lat: float | None, lon: float | None, game_date: str, as_of_date: str) -> dict:
        raise NotImplementedError

    def league_averages_as_of(self, as_of_date: str, season: int) -> dict:
        raise NotImplementedError

    def team_ops_rolling_as_of(self, team_id: int, as_of_date: str, days: int) -> float | None:
        """OPS de equipo en los `days` dias previos a `as_of_date` (nunca
        incluye el propio dia de corte) -- mismo patron point-in-time que
        `team_ops_as_of()`, con ventana movil en vez de acumulado de
        temporada. Candidato de forma reciente para el pilar Trend (bajo
        evaluacion LOSO, ver ROADMAP -- todavia NO wireado en `trend.py`).
        `None` si no hay datos suficientes en la ventana."""
        raise NotImplementedError

    def team_era_rolling_as_of(self, team_id: int, as_of_date: str, days: int) -> float | None:
        """ERA de equipo (pitching agregado del equipo, no solo abridores)
        en los `days` dias previos a `as_of_date` -- mismo proposito que
        `team_ops_rolling_as_of()`, candidato de forma reciente para
        Trend. `None` si no hay datos suficientes en la ventana."""
        raise NotImplementedError

    def hitter_recent_pa_as_of(self, player_id: int, as_of_date: str, days: int = 30) -> int | None:
        """PA de un bateador en los `days` dias previos a `as_of_date` --
        alimenta el criterio de "lesion clave" (Seccion team_quality,
        umbral acordado: 50 PA/30 dias)."""
        raise NotImplementedError

    def pitcher_recent_ip_as_of(self, player_id: int, as_of_date: str, days: int = 30) -> float | None:
        """IP de un pitcher en los `days` dias previos a `as_of_date` --
        mismo proposito que `hitter_recent_pa_as_of` (umbral acordado: 15
        IP/30 dias, pensado para abridores -- los cerradores/relevistas de
        alto apalancamiento ya tienen su propia señal via
        `closer_pitcher_id`, no necesitan cruzar este umbral)."""
        raise NotImplementedError


class MLBStatsAPIProvider(HistoricalStatsProvider):
    """Implementacion real: `stats=byDateRange` con `endDate` = as_of_date
    - 1 dia (nunca incluye el propio dia del corte). Nunca usa
    `stats=season`."""

    def __init__(self):
        # Cache de climatologia SOLO en memoria de esta instancia -- vive y
        # muere con una unica corrida de ingesta (nunca compartida entre runs).
        self._weather_cache: dict[tuple, float | None] = {}

    def _end_date(self, as_of_date: str) -> str:
        cutoff = date.fromisoformat(as_of_date) - timedelta(days=1)
        return cutoff.strftime("%Y-%m-%d")

    def _pitcher_stat_dict_as_of(self, pitcher_id: int, start_date: str, end_date: str) -> dict | None:
        """Fetch crudo compartido -- `pitcher_era_ip_as_of()` y la deteccion
        de cerrador dentro de `bullpen_era_as_of()` leen del MISMO payload
        (era, IP y saves ya vienen juntos en una sola respuesta de la API),
        para no duplicar trafico de red pidiendo dos veces la misma stat."""
        try:
            params = {"stats": "byDateRange", "group": "pitching", "startDate": start_date, "endDate": end_date}
            resp = session.get(f"{MLB_API_BASE}/people/{pitcher_id}/stats", params=params, timeout=INGESTION_REQUEST_TIMEOUT)
            resp.raise_for_status()
            splits = resp.json()["stats"][0]["splits"]
            return splits[0]["stat"] if splits else None
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            logger.debug("Stats as-of fallo para pitcher %s @ [%s,%s]: %s", pitcher_id, start_date, end_date, e)
            return None

    def pitcher_era_ip_as_of(self, pitcher_id, as_of_date, season):
        stat = self._pitcher_stat_dict_as_of(pitcher_id, _season_start(season), self._end_date(as_of_date))
        if stat is None:
            return None
        era, ip_str = _parse_era(stat.get("era")), stat.get("inningsPitched")
        if era is None or ip_str is None:
            return None
        ip = _parse_innings(ip_str)
        starts = stat.get("gamesStarted") or 0
        projected_ip = (ip / starts) if starts > 0 else None
        return {"era": era, "ip": ip, "projected_ip": projected_ip}

    def team_ops_as_of(self, team_id, as_of_date, season):
        try:
            params = {
                "stats": "byDateRange", "group": "hitting",
                "startDate": _season_start(season), "endDate": self._end_date(as_of_date),
            }
            resp = session.get(f"{MLB_API_BASE}/teams/{team_id}/stats", params=params, timeout=INGESTION_REQUEST_TIMEOUT)
            resp.raise_for_status()
            splits = resp.json()["stats"][0]["splits"]
            if not splits:
                return None
            stat = splits[0]["stat"]
            ops = stat.get("ops")
            if ops is None:
                return None
            pa_raw = stat.get("plateAppearances")
            return float(ops), (int(pa_raw) if pa_raw is not None else None)
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            logger.debug("OPS as-of fallo para equipo %s @ %s: %s", team_id, as_of_date, e)
            return None

    def team_fielding_pct_as_of(self, team_id, as_of_date, season):
        try:
            params = {
                "stats": "byDateRange", "group": "fielding",
                "startDate": _season_start(season), "endDate": self._end_date(as_of_date),
            }
            resp = session.get(f"{MLB_API_BASE}/teams/{team_id}/stats", params=params, timeout=INGESTION_REQUEST_TIMEOUT)
            resp.raise_for_status()
            splits = resp.json()["stats"][0]["splits"]
            if not splits:
                return None
            fielding = splits[0]["stat"].get("fielding")
            return float(fielding) if fielding is not None else None
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            logger.debug("Fielding%% as-of fallo para equipo %s @ %s: %s", team_id, as_of_date, e)
            return None

    def bullpen_era_as_of(self, team_id, as_of_date, season):
        # El roster SI soporta un snapshot historico real via `date=` --
        # se pide con la misma fecha de corte que el resto del proveedor
        # para que nunca incluya un movimiento (trade/call-up) posterior.
        try:
            roster_resp = session.get(
                f"{MLB_API_BASE}/teams/{team_id}/roster",
                params={"rosterType": "active", "date": self._end_date(as_of_date)},
                timeout=INGESTION_REQUEST_TIMEOUT,
            )
            roster_resp.raise_for_status()
            roster = roster_resp.json().get("roster", [])
        except requests.RequestException as e:
            logger.debug("roster as-of fallo para equipo %s @ %s: %s", team_id, as_of_date, e)
            return {"era": None, "closer_pitcher_id": None, "ip": 0.0}

        pitcher_ids = [p["person"]["id"] for p in roster if p.get("position", {}).get("abbreviation") == "P"]
        start_date, end_date = _season_start(season), self._end_date(as_of_date)

        total_ip, weighted_era_sum = 0.0, 0.0
        closer_pitcher_id, most_saves = None, 0
        for pid in pitcher_ids:
            stat = self._pitcher_stat_dict_as_of(pid, start_date, end_date)
            if stat is None:
                continue
            era, ip_str, saves = _parse_era(stat.get("era")), stat.get("inningsPitched"), stat.get("saves")
            if era is not None and ip_str is not None:
                ip = _parse_innings(ip_str)
                if ip > 0:
                    weighted_era_sum += era * ip
                    total_ip += ip
            # Cerrador = el relevista con mas saves point-in-time del
            # roster -- misma llamada que ya se hizo arriba para el ERA,
            # cero trafico adicional.
            if saves is not None and int(saves) > most_saves:
                most_saves = int(saves)
                closer_pitcher_id = pid

        return {
            "era": (weighted_era_sum / total_ip) if total_ip > 0 else None,
            "closer_pitcher_id": closer_pitcher_id if most_saves > 0 else None,
            "ip": total_ip,
        }

    def pitcher_command_as_of(self, pitcher_id, as_of_date, season):
        result = {"k_pct": None, "bb_pct": None}
        try:
            params = {
                "stats": "byDateRange", "group": "pitching",
                "startDate": _season_start(season), "endDate": self._end_date(as_of_date),
            }
            resp = session.get(f"{MLB_API_BASE}/people/{pitcher_id}/stats", params=params, timeout=INGESTION_REQUEST_TIMEOUT)
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
            logger.debug("K%%/BB%% as-of fallo para pitcher %s @ %s: %s", pitcher_id, as_of_date, e)
        return result

    def historical_weather(self, lat, lon, game_date, as_of_date):
        """Climatologia point-in-time -- NUNCA consulta el clima real de
        `game_date` (esa era la unica variable del proveedor original que
        no respetaba un corte de fecha). Promedia temperatura Y viento de
        la MISMA ventana de calendario en los `WEATHER_CLIMATOLOGY_YEARS`
        años ANTERIORES al año de `as_of_date` -- cada ventana cae
        enteramente en un año pasado completo, imposible que se solape.

        `wind_speed` se agrega junto con `temp_f` en la misma llamada (ya
        estaba disponible en la respuesta de Open-Meteo, solo faltaba
        pedirla) -- antes de esto `context.py` nunca podia detectar viento
        extremo en un juego historico porque `weather_wind_speed` quedaba
        siempre en None."""
        result = {"temp_f": None, "wind_speed": None}
        if lat is None or lon is None or not game_date:
            return result

        cache_key = (round(lat, 2), round(lon, 2), game_date, as_of_date)
        if cache_key in self._weather_cache:
            return dict(self._weather_cache[cache_key])

        try:
            cutoff_year = date.fromisoformat(as_of_date).year
            month, day = int(game_date[5:7]), int(game_date[8:10])
        except ValueError:
            return result

        temps: list[float] = []
        winds: list[float] = []
        for year_offset in range(1, WEATHER_CLIMATOLOGY_YEARS + 1):
            yr = cutoff_year - year_offset
            try:
                center = date(yr, month, day)
            except ValueError:
                continue  # 29 de febrero en año no bisiesto
            window_start = center - timedelta(days=WEATHER_WINDOW_DAYS)
            window_end = center + timedelta(days=WEATHER_WINDOW_DAYS)
            if window_end >= date.fromisoformat(as_of_date):
                continue  # defensa redundante por construccion -- nunca deberia dispararse
            try:
                resp = session.get(OPEN_METEO_ARCHIVE_BASE, params={
                    "latitude": lat, "longitude": lon,
                    "start_date": window_start.isoformat(), "end_date": window_end.isoformat(),
                    "hourly": "temperature_2m,windspeed_10m", "temperature_unit": "fahrenheit",
                    "wind_speed_unit": "mph", "timezone": "UTC",
                }, timeout=INGESTION_REQUEST_TIMEOUT)
                resp.raise_for_status()
                hourly = resp.json().get("hourly", {})
                temps.extend(v for v in hourly.get("temperature_2m", []) if v is not None)
                winds.extend(v for v in hourly.get("windspeed_10m", []) if v is not None)
            except (requests.RequestException, KeyError, ValueError, TypeError) as e:
                logger.debug("climatologia %s fallo para %s: %s", yr, game_date, e)

        result["temp_f"] = (sum(temps) / len(temps)) if temps else None
        result["wind_speed"] = (sum(winds) / len(winds)) if winds else None
        self._weather_cache[cache_key] = dict(result)
        return result

    def team_ops_rolling_as_of(self, team_id, as_of_date, days):
        start = (date.fromisoformat(as_of_date) - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            params = {"stats": "byDateRange", "group": "hitting", "startDate": start, "endDate": self._end_date(as_of_date)}
            resp = session.get(f"{MLB_API_BASE}/teams/{team_id}/stats", params=params, timeout=INGESTION_REQUEST_TIMEOUT)
            resp.raise_for_status()
            splits = resp.json()["stats"][0]["splits"]
            if not splits:
                return None
            ops = splits[0]["stat"].get("ops")
            return float(ops) if ops is not None else None
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            logger.debug("OPS rolling (%sd) as-of fallo para equipo %s @ %s: %s", days, team_id, as_of_date, e)
            return None

    def team_era_rolling_as_of(self, team_id, as_of_date, days):
        start = (date.fromisoformat(as_of_date) - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            params = {"stats": "byDateRange", "group": "pitching", "startDate": start, "endDate": self._end_date(as_of_date)}
            resp = session.get(f"{MLB_API_BASE}/teams/{team_id}/stats", params=params, timeout=INGESTION_REQUEST_TIMEOUT)
            resp.raise_for_status()
            splits = resp.json()["stats"][0]["splits"]
            if not splits:
                return None
            return _parse_era(splits[0]["stat"].get("era"))
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            logger.debug("ERA rolling (%sd) as-of fallo para equipo %s @ %s: %s", days, team_id, as_of_date, e)
            return None

    def hitter_recent_pa_as_of(self, player_id, as_of_date, days=30):
        start = (date.fromisoformat(as_of_date) - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            params = {"stats": "byDateRange", "group": "hitting", "startDate": start, "endDate": self._end_date(as_of_date)}
            resp = session.get(f"{MLB_API_BASE}/people/{player_id}/stats", params=params, timeout=INGESTION_REQUEST_TIMEOUT)
            resp.raise_for_status()
            splits = resp.json()["stats"][0]["splits"]
            if not splits:
                return None
            pa = splits[0]["stat"].get("plateAppearances")
            return int(pa) if pa is not None else None
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            logger.debug("PA reciente as-of fallo para jugador %s @ %s: %s", player_id, as_of_date, e)
            return None

    def pitcher_recent_ip_as_of(self, player_id, as_of_date, days=30):
        start = (date.fromisoformat(as_of_date) - timedelta(days=days)).strftime("%Y-%m-%d")
        stat = self._pitcher_stat_dict_as_of(player_id, start, self._end_date(as_of_date))
        if stat is None:
            return None
        ip_str = stat.get("inningsPitched")
        return _parse_innings(ip_str) if ip_str is not None else None

    def league_averages_as_of(self, as_of_date, season):
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
            logger.debug("OPS de liga as-of fallo @ %s: %s", as_of_date, e)

        try:
            resp = session.get(
                f"{MLB_API_BASE}/teams/stats",
                params={"stats": "byDateRange", "group": "pitching", "sportId": 1, "startDate": start_date, "endDate": end_date},
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
            logger.debug("ERA de liga as-of fallo @ %s: %s", as_of_date, e)

        try:
            resp = session.get(
                f"{MLB_API_BASE}/teams/stats",
                params={"stats": "byDateRange", "group": "hitting", "sportId": 1, "startDate": start_date, "endDate": end_date},
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
            logger.debug("carreras/juego de liga as-of fallo @ %s: %s", as_of_date, e)

        return result
