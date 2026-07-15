"""Reconstruye un `GameSnapshot` (Seccion 3.1 del spec) punto-en-el-tiempo
para un juego YA JUGADO, usando el `HistoricalStatsProvider` -- nunca lee
`data_sources/stats.py` (season-cumulative, sin corte de fecha). Usa el
mismo `build_game_snapshot()` de `jsa/domain/models.py` que produccion en
vivo, para que el snapshot resultante sea indistinguible (mismo shape,
mismo hash determinista) de uno generado el dia del juego."""

from __future__ import annotations

from jsa.data_sources import park_factors
from jsa.domain.models import GameSnapshot, build_game_snapshot
from jsa.historical.injuries import InjuryIndex, is_injured_as_of, key_injuries_as_of
from jsa.historical.point_in_time_provider import HistoricalStatsProvider


def reconstruct_snapshot(
    *,
    game_pk: int,
    game_date: str,
    season: int,
    home_team: str,
    away_team: str,
    home_team_id: int,
    away_team_id: int,
    home_pitcher_id: int | None,
    away_pitcher_id: int | None,
    is_double_header: bool,
    provider: HistoricalStatsProvider,
    away_team_previous_park_id: int | None = None,
    injury_index: InjuryIndex | None = None,
) -> GameSnapshot:
    """`game_date` es tambien `as_of_date`: todo dato pedido al provider
    queda estrictamente anterior a la fecha del juego (nunca incluye el
    propio dia), igual que produccion en vivo solo ve datos hasta ayer.

    `away_team_previous_park_id`: team_id del estadio donde el equipo
    visitante jugo su partido inmediato anterior (Seccion 5: "el visitante
    es quien viaja") -- se calcula UNA vez por temporada en `pipeline.py` a
    partir del schedule ya fetcheado (`fetch_season_games`), nunca pide una
    llamada de red adicional aqui. `None` si no hay partido anterior
    conocido (primer juego de la temporada para ese equipo) -- en ese caso
    `travel_distance` queda en None, no se aproxima.

    `injury_index`: `InjuryIndex` ya construido UNA vez por temporada en
    `pipeline.py` (`jsa/historical/injuries.py`) -- alimenta
    `home/away_key_injuries` y, cruzado contra el `closer_pitcher_id` que
    ya devuelve `bullpen_era_as_of()`, `home/away_closer_available`. `None`
    deja ambos campos en su default (lista vacia / None), nunca se
    aproxima."""
    home_era_ip = provider.pitcher_era_ip_as_of(home_pitcher_id, game_date, season) if home_pitcher_id else None
    away_era_ip = provider.pitcher_era_ip_as_of(away_pitcher_id, game_date, season) if away_pitcher_id else None
    home_cmd = provider.pitcher_command_as_of(home_pitcher_id, game_date, season) if home_pitcher_id else {}
    away_cmd = provider.pitcher_command_as_of(away_pitcher_id, game_date, season) if away_pitcher_id else {}

    home_ops_result = provider.team_ops_as_of(home_team_id, game_date, season)
    away_ops_result = provider.team_ops_as_of(away_team_id, game_date, season)

    home_bullpen = provider.bullpen_era_as_of(home_team_id, game_date, season) or {}
    away_bullpen = provider.bullpen_era_as_of(away_team_id, game_date, season) or {}

    home_key_injuries: list[str] = []
    away_key_injuries: list[str] = []
    home_closer_available: bool | None = None
    away_closer_available: bool | None = None
    if injury_index is not None:
        home_key_injuries = key_injuries_as_of(injury_index, home_team_id, game_date)
        away_key_injuries = key_injuries_as_of(injury_index, away_team_id, game_date)
        home_closer_id = home_bullpen.get("closer_pitcher_id")
        away_closer_id = away_bullpen.get("closer_pitcher_id")
        if home_closer_id is not None:
            home_closer_available = not is_injured_as_of(injury_index, home_closer_id, game_date)
        if away_closer_id is not None:
            away_closer_available = not is_injured_as_of(injury_index, away_closer_id, game_date)

    league = provider.league_averages_as_of(game_date, season)

    park = park_factors.get_park_info(home_team_id)  # tabla estatica, sin riesgo de look-ahead
    weather = provider.historical_weather(park["lat"], park["lon"], game_date, game_date)

    home_k_bb = None
    if home_cmd.get("k_pct") is not None and home_cmd.get("bb_pct") is not None:
        home_k_bb = home_cmd["k_pct"] - home_cmd["bb_pct"]
    away_k_bb = None
    if away_cmd.get("k_pct") is not None and away_cmd.get("bb_pct") is not None:
        away_k_bb = away_cmd["k_pct"] - away_cmd["bb_pct"]

    return build_game_snapshot(
        game_id=str(game_pk),
        game_pk=game_pk,
        game_date=game_date,
        season=season,
        home_team=home_team,
        away_team=away_team,
        home_starter_xera=home_era_ip.get("era") if home_era_ip else None,
        away_starter_xera=away_era_ip.get("era") if away_era_ip else None,
        home_starter_ip_sample=home_era_ip.get("ip") if home_era_ip else None,
        away_starter_ip_sample=away_era_ip.get("ip") if away_era_ip else None,
        # ip / games started point-in-time (mismo proxy que
        # data_sources/stats.py::get_pitcher_command() en produccion) --
        # alimenta long_outing/short_outing_bullpen_game en el Context
        # Detector.
        home_starter_projected_ip=home_era_ip.get("projected_ip") if home_era_ip else None,
        away_starter_projected_ip=away_era_ip.get("projected_ip") if away_era_ip else None,
        home_starter_k_bb_pct=home_k_bb,
        away_starter_k_bb_pct=away_k_bb,
        home_ops=home_ops_result[0] if home_ops_result else None,
        away_ops=away_ops_result[0] if away_ops_result else None,
        home_ops_pa_sample=home_ops_result[1] if home_ops_result else None,
        away_ops_pa_sample=away_ops_result[1] if away_ops_result else None,
        home_bullpen_era=home_bullpen.get("era"),
        away_bullpen_era=away_bullpen.get("era"),
        home_closer_available=home_closer_available,
        away_closer_available=away_closer_available,
        home_key_injuries=home_key_injuries,
        away_key_injuries=away_key_injuries,
        is_double_header=is_double_header,
        weather_temp_f=weather.get("temp_f"),
        weather_wind_speed=weather.get("wind_speed"),
        travel_distance=park_factors.distance_miles(away_team_previous_park_id, home_team_id),
        park_factor=park["park_factor"],
        starters_confirmed=bool(home_pitcher_id and away_pitcher_id),
        lineups_official=False,
        bullpen_usage_known=False,
        no_last_minute_changes=False,
        league_avg_era=league.get("league_era"),
        league_avg_ops=league.get("league_ops"),
        league_avg_runs_per_game=league.get("league_runs_per_game"),
    )
