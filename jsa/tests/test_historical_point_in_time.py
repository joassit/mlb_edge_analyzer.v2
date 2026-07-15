"""FakeProvider deterministico -- mismo patron que
`mlb_edge_analyzer.v2/tests/test_historical_point_in_time.py::FakeProvider`,
reescrito contra la interfaz de `jsa/historical/point_in_time_provider.py`."""

from __future__ import annotations

import pytest

from jsa.historical.point_in_time_provider import HistoricalStatsProvider
from jsa.historical.snapshot_reconstruction import reconstruct_snapshot


class FakeProvider(HistoricalStatsProvider):
    def __init__(
        self, era=3.50, ip=80.0, ops=0.760, pa=300, bullpen_era=4.10, temp_f=72.0, wind_speed=None,
        closer_pitcher_id=None, recent_pa=100, recent_ip=20.0, projected_ip=None,
    ):
        self.era, self.ip, self.ops, self.pa, self.bullpen_era = era, ip, ops, pa, bullpen_era
        self.temp_f, self.wind_speed = temp_f, wind_speed
        self.closer_pitcher_id = closer_pitcher_id
        self.recent_pa, self.recent_ip = recent_pa, recent_ip
        self.projected_ip = projected_ip
        self.calls: list[tuple] = []

    def pitcher_era_ip_as_of(self, pitcher_id, as_of_date, season):
        self.calls.append(("pitcher_era_ip_as_of", pitcher_id, as_of_date, season))
        if not pitcher_id:
            return None
        return {"era": self.era, "ip": self.ip, "projected_ip": self.projected_ip}

    def team_ops_as_of(self, team_id, as_of_date, season):
        self.calls.append(("team_ops_as_of", team_id, as_of_date, season))
        return (self.ops, self.pa)

    def bullpen_era_as_of(self, team_id, as_of_date, season):
        self.calls.append(("bullpen_era_as_of", team_id, as_of_date, season))
        return {"era": self.bullpen_era, "closer_pitcher_id": self.closer_pitcher_id}

    def pitcher_command_as_of(self, pitcher_id, as_of_date, season):
        return {"k_pct": 0.24, "bb_pct": 0.07}

    def historical_weather(self, lat, lon, game_date, as_of_date):
        return {"temp_f": self.temp_f, "wind_speed": self.wind_speed}

    def league_averages_as_of(self, as_of_date, season):
        return {"league_ops": 0.750, "league_era": 4.30, "league_runs_per_game": 4.5}

    def hitter_recent_pa_as_of(self, player_id, as_of_date, days=30):
        return self.recent_pa

    def pitcher_recent_ip_as_of(self, player_id, as_of_date, days=30):
        return self.recent_ip


def _reconstruct(**overrides):
    fields = dict(
        game_pk=717468, game_date="2022-04-15", season=2022, home_team="New York Yankees", away_team="Boston Red Sox",
        home_team_id=147, away_team_id=111, home_pitcher_id=1001, away_pitcher_id=1002, is_double_header=False,
        provider=FakeProvider(),
    )
    fields.update(overrides)
    return reconstruct_snapshot(**fields)


def test_reconstruction_produces_valid_hashed_snapshot():
    snap = _reconstruct()
    assert snap.snapshot_hash == snap.compute_hash()
    assert snap.home_starter_xera == 3.50
    assert snap.away_ops == 0.760


def test_as_of_date_passed_is_the_game_date_never_a_later_date():
    """Regla dura de integridad punto-en-el-tiempo: TODAS las llamadas al
    provider deben usar exactamente `game_date` como `as_of_date` -- nunca
    la fecha de HOY (que seria fuga de informacion futura si se
    reconstruye un snapshot de 2022 en 2026)."""
    provider = FakeProvider()
    _reconstruct(provider=provider, game_date="2022-04-15")
    as_of_dates_used = {call[2] for call in provider.calls}
    assert as_of_dates_used == {"2022-04-15"}


def test_reconstruction_never_calls_live_production_stats_source():
    """Los insumos deben venir del provider point-in-time, nunca de
    jsa/data_sources/stats.py (season-cumulative sin corte de fecha)."""
    import inspect

    from jsa.historical import snapshot_reconstruction

    source = inspect.getsource(snapshot_reconstruction)
    assert "data_sources.stats" not in source
    assert "from jsa.data_sources import stats" not in source


def test_missing_pitcher_id_yields_none_stats_not_a_crash():
    snap = _reconstruct(home_pitcher_id=None)
    assert snap.home_starter_xera is None
    assert snap.starters_confirmed is False


def test_reconstruction_is_deterministic_for_same_inputs():
    a = _reconstruct(provider=FakeProvider())
    b = _reconstruct(provider=FakeProvider())
    assert a.snapshot_hash == b.snapshot_hash


def test_reconstruction_populates_wind_speed_from_provider():
    snap = _reconstruct(provider=FakeProvider(wind_speed=18.0))
    assert snap.weather_wind_speed == 18.0


def test_reconstruction_wind_speed_is_none_without_data():
    snap = _reconstruct(provider=FakeProvider())
    assert snap.weather_wind_speed is None


def test_reconstruction_computes_travel_distance_from_previous_park_id():
    from jsa.data_sources import park_factors

    # away_team_id=111 (Red Sox) venia de jugar en Los Angeles (119) antes
    # de este juego en Yankee Stadium (147).
    snap = _reconstruct(away_team_previous_park_id=119)
    expected = park_factors.distance_miles(119, 147)
    assert snap.travel_distance == pytest.approx(expected)
    assert snap.travel_distance > 2000  # cruza EXTREME_TRAVEL_MILES


def test_reconstruction_travel_distance_is_none_without_previous_park():
    snap = _reconstruct(away_team_previous_park_id=None)
    assert snap.travel_distance is None


def test_reconstruction_projected_ip_is_none_without_starts():
    snap = _reconstruct(provider=FakeProvider(projected_ip=None))
    assert snap.home_starter_projected_ip is None


def test_projected_ip_from_historical_reconstruction_flows_into_long_outing_signal():
    """Punta a punta: `home/away_starter_projected_ip` reconstruido desde el
    provider point-in-time debe disparar `long_outing` en el Context
    Detector -- antes de este fix, `projected_ip` siempre era None sobre
    datos historicos y esto era estructuralmente imposible (Seccion 5/6.3:
    long_outing mueve pesos reales starter/bullpen via el Rule Engine)."""
    from jsa.engine.context_detector import detect_context

    snap = _reconstruct(provider=FakeProvider(projected_ip=7.0))  # >= LONG_OUTING_IP
    context = detect_context(snap)
    assert context.long_outing is True


def test_extreme_travel_from_historical_reconstruction_flows_into_context_pillar():
    """Punta a punta: un `travel_distance` reconstruido desde un
    `away_team_previous_park_id` real debe disparar `extreme_travel` en el
    Context Detector y mover el advantage del pilar `context` -- antes de
    este fix, `travel_distance` siempre era None y esto era estructuralmente
    imposible sobre datos historicos."""
    from jsa.engine.context_detector import detect_context
    from jsa.engine.pillars.context import evaluate as evaluate_context

    snap = _reconstruct(away_team_previous_park_id=119)  # LA -> Nueva York
    context = detect_context(snap)
    assert context.extreme_travel is True

    advantage = evaluate_context(snap, context)
    assert advantage.advantage == -1  # penaliza al visitante, ver context.py
