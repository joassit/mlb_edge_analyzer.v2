"""
Motor de reconstrucción de variables point-in-time -- recibe fecha, equipo
y pitcher, y devuelve únicamente variables calculadas con información
DISPONIBLE ANTES de esa fecha (ver historical_engine/point_in_time_provider.py
para el porqué de no reutilizar data/stats.py).

Protecciones explícitas contra:
  - Data Leakage / Look-Ahead Bias / Future Information Leakage: ver
    `_assert_no_lookahead()`, que se llama SIEMPRE antes de golpear
    cualquier fuente de datos -- si as_of_date >= game_date, lanza
    LookAheadBiasError en vez de silenciosamente calcular con datos que
    incluirían el propio juego o partidos posteriores.
  - Survivorship Bias: `reconstruct_features()` no filtra ni excluye
    pitchers/equipos por su desempeño posterior o su continuidad en la
    liga -- reconstruye exactamente el pitcher/equipo que jugó ESE juego,
    tal como constaba en el roster/probable pitcher oficial de esa fecha,
    sin importar qué pasó después con esa persona o ese equipo.

Ver tests/test_historical_point_in_time.py para la prueba automatizada de
cada una de estas protecciones.
"""

from dataclasses import dataclass
from datetime import date

from data.park_factors import get_park_info  # tabla estática, sin estado ni fecha -- ver docstring ahí
from historical_engine.point_in_time_provider import HistoricalStatsProvider, MLBStatsAPIProvider


class LookAheadBiasError(ValueError):
    """Se lanza cuando alguien intenta reconstruir variables con una fecha
    de corte que no es estrictamente anterior a la fecha del juego -- señal
    de un bug de llamado (nunca debe ocurrir en un pipeline histórico bien
    formado), no una condición esperada que se deba tolerar en silencio."""


def _assert_no_lookahead(as_of_date: str, game_date: str) -> None:
    if as_of_date >= game_date:
        raise LookAheadBiasError(
            f"as_of_date={as_of_date!r} no es estrictamente anterior a game_date={game_date!r} -- "
            f"reconstruir variables con esta fecha de corte filtraría información del propio juego "
            f"o de partidos posteriores (look-ahead bias). Usa una fecha anterior a game_date."
        )


@dataclass
class PointInTimeFeatures:
    """Resultado de reconstruir un lado (away u home) de un juego -- campos
    en None cuando el proveedor no pudo resolver el dato (mismo criterio de
    'nunca inventar' que el resto del proyecto: ausencia explícita, no un
    valor por default oculto)."""
    era: float | None = None
    innings_pitched: float | None = None
    ops: float | None = None
    bullpen_era: float | None = None
    k_pct: float | None = None
    bb_pct: float | None = None
    days_rest: int | None = None
    last_outing_pitches: int | None = None


def reconstruct_team_pitcher_features(
    team_id: int, pitcher_id: int | None, as_of_date: str, game_date: str, season: int,
    provider: HistoricalStatsProvider | None = None,
) -> PointInTimeFeatures:
    """
    Reconstruye ERA/IP/OPS/bullpen/K%/BB%/descanso de UN lado (un equipo +
    su pitcher probable) usando solo información antes de `as_of_date`.

    `provider=None` usa la implementación real (MLBStatsAPIProvider, llama
    a la MLB Stats API); los tests inyectan un FakeProvider determinista.
    """
    _assert_no_lookahead(as_of_date, game_date)
    provider = provider or MLBStatsAPIProvider()

    era = ip = None
    k_pct = bb_pct = None
    days_rest = last_outing_pitches = None

    if pitcher_id is not None:
        era_ip = provider.pitcher_era_ip_as_of(pitcher_id, as_of_date, season)
        if era_ip is not None:
            era, ip = era_ip
        cmd = provider.pitcher_command_as_of(pitcher_id, as_of_date, season)
        k_pct, bb_pct = cmd.get("k_pct"), cmd.get("bb_pct")
        rest = provider.pitcher_rest_as_of(pitcher_id, as_of_date, season)
        days_rest, last_outing_pitches = rest.get("days_rest"), rest.get("last_outing_pitches")

    ops = provider.team_ops_as_of(team_id, as_of_date, season)
    bullpen_era = provider.bullpen_era_as_of(team_id, as_of_date, season)

    return PointInTimeFeatures(
        era=era, innings_pitched=ip, ops=ops, bullpen_era=bullpen_era,
        k_pct=k_pct, bb_pct=bb_pct, days_rest=days_rest, last_outing_pitches=last_outing_pitches,
    )


def reconstruct_game_features(
    game: dict, as_of_date: str, season: int, provider: HistoricalStatsProvider | None = None,
) -> dict:
    """
    Reconstruye TODAS las variables de un juego histórico (ambos lados +
    parque + clima) point-in-time. `game` trae al menos: game_date,
    away_team_id, home_team_id, away_pitcher_id, home_pitcher_id,
    game_time (ISO, opcional, para clima).

    park_factor se lee de la tabla estática (data/park_factors.py) -- no
    depende de la fecha (el modelo de producción tampoco lo trata como
    variable temporal, ver main.py::_analyze_one_game), así que no hay
    look-ahead posible ahí: es la misma tabla sin importar cuándo se
    consulte.
    """
    game_date = game["game_date"]
    _assert_no_lookahead(as_of_date, game_date)
    provider = provider or MLBStatsAPIProvider()

    away = reconstruct_team_pitcher_features(
        game["away_team_id"], game.get("away_pitcher_id"), as_of_date, game_date, season, provider,
    )
    home = reconstruct_team_pitcher_features(
        game["home_team_id"], game.get("home_pitcher_id"), as_of_date, game_date, season, provider,
    )

    park = get_park_info(game["home_team_id"])
    weather = provider.historical_weather(park.get("lat"), park.get("lon"), game_date)
    league = provider.league_averages_as_of(as_of_date, season)

    return {
        "as_of_date": as_of_date,
        "league_ops": league.get("league_ops"),
        "league_era": league.get("league_era"),
        "league_avg_runs_per_game": league.get("league_runs_per_game"),
        "away_era": away.era, "home_era": home.era,
        "away_innings_pitched": away.innings_pitched, "home_innings_pitched": home.innings_pitched,
        "away_ops": away.ops, "home_ops": home.ops,
        "away_bullpen_era": away.bullpen_era, "home_bullpen_era": home.bullpen_era,
        "away_k_pct": away.k_pct, "home_k_pct": home.k_pct,
        "away_bb_pct": away.bb_pct, "home_bb_pct": home.bb_pct,
        "away_days_rest": away.days_rest, "home_days_rest": home.days_rest,
        "park_name": park.get("name"), "park_factor": park.get("park_factor"),
        "temp_f": weather.get("temp_f"),
    }


def default_as_of_date(game_date: str) -> str:
    """Fecha de corte por default: el propio game_date (el primer valor que
    _assert_no_lookahead rechazaría) MENOS 1 día -- reconstruye "cómo se
    veía el mundo el día antes del juego", el punto de corte más ajustado
    posible sin filtrar el resultado del propio juego."""
    d = date.fromisoformat(game_date)
    from datetime import timedelta
    return (d - timedelta(days=1)).strftime("%Y-%m-%d")
