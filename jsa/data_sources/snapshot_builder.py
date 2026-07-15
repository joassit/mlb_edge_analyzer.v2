"""Ensambla un `GameSnapshot` (Seccion 3.1) a partir de las fuentes crudas.

Punto unico donde las fuentes de datos (MLB Stats API, Open-Meteo, tabla
estatica de parques) se convierten en el contrato congelado que el resto
del sistema consume. Ningun otro modulo debe leer estas fuentes
directamente -- eso es lo que garantiza que "evaluar en vivo" y "recalcular
desde un snapshot ya guardado" (futuro backtest) usen exactamente los
mismos datos, nunca datos frescos a mitad de un recalculo historico.

Limitaciones honestas de esta entrega (documentadas, no escondidas -- ver
`jsa/docs/ROADMAP.md`):
- `home_starter_xera`/`xfip`: la MLB Stats API no expone xERA/xFIP de
  Statcast. Se usa ERA real de temporada como proxy explicito.
- `lineups_official`, `bullpen_usage_known`, `no_last_minute_changes`,
  `home_closer_available`/`away_closer_available`,
  `home_bullpen_ip_last_3_days`/`away_...`, `home_key_injuries`/`away_...`:
  sin fuente wireada todavia -- quedan en su valor por defecto
  (`False`/`None`/`[]`), nunca inventados. Esto baja el CRI de forma
  realista en vez de aparentar mas confiabilidad de la que hay.
  `travel_distance` y `weather_wind_speed` SI tienen fuente real (ver abajo).
"""

from __future__ import annotations

import logging

from jsa.config import SEASON
from jsa.data_sources import mlb_api, park_factors, stats, weather
from jsa.domain.models import GameSnapshot, build_game_snapshot

logger = logging.getLogger("jsa")


def build_snapshot_from_game(
    game: dict,
    weather_by_home_team: dict[int, dict],
    league_context: dict,
    season: int = SEASON,
    travel_by_away_team: dict[int, float | None] | None = None,
) -> GameSnapshot:
    """`game`: un dict devuelto por `mlb_api.get_schedule()`. `league_context`:
    {"league_avg_era", "league_avg_ops", "league_avg_runs_per_game"} --
    calculado UNA vez por corrida (ver `main.py`) y congelado igual para
    todos los juegos del dia, nunca recalculado dentro de un pilar (ver
    comentario en `GameSnapshot.league_avg_era`). Hace las llamadas de stats
    necesarias (bloqueantes) y devuelve un `GameSnapshot` ya hasheado.

    `travel_by_away_team`: salida de `data_sources.travel.preload_travel_distances()`,
    precalculada UNA vez por corrida igual que `weather_by_home_team` --
    opcional (`None`) para no romper callers/tests existentes que todavia
    no la pasan."""
    home_id, away_id = game["home_team_id"], game["away_team_id"]
    home_pid, away_pid = game.get("home_pitcher_id"), game.get("away_pitcher_id")

    home_era_ip = stats.get_pitcher_era_ip(home_pid, season) if home_pid else None
    away_era_ip = stats.get_pitcher_era_ip(away_pid, season) if away_pid else None
    home_cmd = stats.get_pitcher_command(home_pid, season) if home_pid else {}
    away_cmd = stats.get_pitcher_command(away_pid, season) if away_pid else {}

    home_ops = stats.get_team_ops(home_id, season)
    away_ops = stats.get_team_ops(away_id, season)
    home_pa = stats.get_team_ops_pa_sample(home_id, season)
    away_pa = stats.get_team_ops_pa_sample(away_id, season)

    home_bullpen_era = stats.get_bullpen_era(home_id, season)
    away_bullpen_era = stats.get_bullpen_era(away_id, season)

    park = park_factors.get_park_info(home_id)
    game_weather = weather_by_home_team.get(home_id) or weather.get_game_weather(
        park["lat"], park["lon"], game.get("game_time")
    )

    return build_game_snapshot(
        game_id=str(game["game_pk"]),
        game_pk=game["game_pk"],
        game_date=_parse_official_date(game),
        season=season,
        home_team=game["home_team"],
        away_team=game["away_team"],
        home_starter_projected_ip=home_cmd.get("projected_ip"),
        away_starter_projected_ip=away_cmd.get("projected_ip"),
        home_starter_xera=home_era_ip[0] if home_era_ip else None,
        away_starter_xera=away_era_ip[0] if away_era_ip else None,
        home_starter_xfip=None,
        away_starter_xfip=None,
        home_starter_k_bb_pct=home_cmd.get("k_bb_pct"),
        away_starter_k_bb_pct=away_cmd.get("k_bb_pct"),
        home_starter_barrel_pct_allowed=None,
        home_starter_ip_sample=home_era_ip[1] if home_era_ip else home_cmd.get("ip_sample"),
        away_starter_ip_sample=away_era_ip[1] if away_era_ip else away_cmd.get("ip_sample"),
        home_ops=home_ops,
        away_ops=away_ops,
        home_ops_pa_sample=home_pa,
        away_ops_pa_sample=away_pa,
        home_bullpen_era=home_bullpen_era,
        away_bullpen_era=away_bullpen_era,
        home_bullpen_ip_last_3_days=None,
        away_bullpen_ip_last_3_days=None,
        home_closer_available=None,
        away_closer_available=None,
        home_key_injuries=[],
        away_key_injuries=[],
        is_double_header=bool(game.get("is_double_header", False)),
        travel_distance=(travel_by_away_team or {}).get(away_id),
        weather_temp_f=game_weather.get("temp_f"),
        weather_wind_speed=game_weather.get("wind_mph"),
        park_factor=park["park_factor"],
        starters_confirmed=bool(home_pid and away_pid),
        lineups_official=False,
        bullpen_usage_known=False,
        no_last_minute_changes=False,
        league_avg_era=league_context.get("league_avg_era"),
        league_avg_ops=league_context.get("league_avg_ops"),
        league_avg_runs_per_game=league_context.get("league_avg_runs_per_game"),
    )


def build_league_context(season: int = SEASON) -> dict:
    """Se llama UNA vez por corrida (no por juego) -- ver docstring de
    `build_snapshot_from_game`."""
    return {
        "league_avg_era": stats.get_league_era(season),
        "league_avg_ops": stats.get_league_ops(season),
        "league_avg_runs_per_game": stats.get_league_runs_per_game(season),
    }


def _parse_official_date(game: dict):
    from datetime import date as _date

    official = game.get("game_date_official")
    if official:
        return _date.fromisoformat(official)
    return _date.today()
