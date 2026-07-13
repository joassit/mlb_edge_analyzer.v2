"""Reconstruye un `GameSnapshot` (Seccion 3.1 del spec) punto-en-el-tiempo
para un juego YA JUGADO, usando el `HistoricalStatsProvider` -- nunca lee
`data_sources/stats.py` (season-cumulative, sin corte de fecha). Usa el
mismo `build_game_snapshot()` de `jsa/domain/models.py` que produccion en
vivo, para que el snapshot resultante sea indistinguible (mismo shape,
mismo hash determinista) de uno generado el dia del juego."""

from __future__ import annotations

from jsa.data_sources import park_factors
from jsa.domain.models import GameSnapshot, build_game_snapshot
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
) -> GameSnapshot:
    """`game_date` es tambien `as_of_date`: todo dato pedido al provider
    queda estrictamente anterior a la fecha del juego (nunca incluye el
    propio dia), igual que produccion en vivo solo ve datos hasta ayer."""
    home_era_ip = provider.pitcher_era_ip_as_of(home_pitcher_id, game_date, season) if home_pitcher_id else None
    away_era_ip = provider.pitcher_era_ip_as_of(away_pitcher_id, game_date, season) if away_pitcher_id else None
    home_cmd = provider.pitcher_command_as_of(home_pitcher_id, game_date, season) if home_pitcher_id else {}
    away_cmd = provider.pitcher_command_as_of(away_pitcher_id, game_date, season) if away_pitcher_id else {}

    home_ops_result = provider.team_ops_as_of(home_team_id, game_date, season)
    away_ops_result = provider.team_ops_as_of(away_team_id, game_date, season)

    home_bullpen_era = provider.bullpen_era_as_of(home_team_id, game_date, season)
    away_bullpen_era = provider.bullpen_era_as_of(away_team_id, game_date, season)

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
        home_starter_xera=home_era_ip[0] if home_era_ip else None,
        away_starter_xera=away_era_ip[0] if away_era_ip else None,
        home_starter_ip_sample=home_era_ip[1] if home_era_ip else None,
        away_starter_ip_sample=away_era_ip[1] if away_era_ip else None,
        # projected_ip no tiene una fuente point-in-time confiable en esta
        # entrega (ver jsa/docs/ROADMAP.md) -- se deja None a proposito, no
        # se aproxima con datos que podrian filtrar informacion futura.
        home_starter_projected_ip=None,
        away_starter_projected_ip=None,
        home_starter_k_bb_pct=home_k_bb,
        away_starter_k_bb_pct=away_k_bb,
        home_ops=home_ops_result[0] if home_ops_result else None,
        away_ops=away_ops_result[0] if away_ops_result else None,
        home_ops_pa_sample=home_ops_result[1] if home_ops_result else None,
        away_ops_pa_sample=away_ops_result[1] if away_ops_result else None,
        home_bullpen_era=home_bullpen_era,
        away_bullpen_era=away_bullpen_era,
        is_double_header=is_double_header,
        weather_temp_f=weather.get("temp_f"),
        park_factor=park["park_factor"],
        starters_confirmed=bool(home_pitcher_id and away_pitcher_id),
        lineups_official=False,
        bullpen_usage_known=False,
        no_last_minute_changes=False,
        league_avg_era=league.get("league_era"),
        league_avg_ops=league.get("league_ops"),
        league_avg_runs_per_game=league.get("league_runs_per_game"),
    )
