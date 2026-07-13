"""FakeProvider deterministico -- mismo patron que
`mlb_edge_analyzer.v2/tests/test_historical_point_in_time.py::FakeProvider`,
reescrito contra la interfaz de `jsa/historical/point_in_time_provider.py`."""

from __future__ import annotations

from jsa.historical.point_in_time_provider import HistoricalStatsProvider
from jsa.historical.snapshot_reconstruction import reconstruct_snapshot


class FakeProvider(HistoricalStatsProvider):
    def __init__(self, era=3.50, ip=80.0, ops=0.760, pa=300, bullpen_era=4.10, temp_f=72.0):
        self.era, self.ip, self.ops, self.pa, self.bullpen_era, self.temp_f = era, ip, ops, pa, bullpen_era, temp_f
        self.calls: list[tuple] = []

    def pitcher_era_ip_as_of(self, pitcher_id, as_of_date, season):
        self.calls.append(("pitcher_era_ip_as_of", pitcher_id, as_of_date, season))
        return (self.era, self.ip) if pitcher_id else None

    def team_ops_as_of(self, team_id, as_of_date, season):
        self.calls.append(("team_ops_as_of", team_id, as_of_date, season))
        return (self.ops, self.pa)

    def bullpen_era_as_of(self, team_id, as_of_date, season):
        self.calls.append(("bullpen_era_as_of", team_id, as_of_date, season))
        return self.bullpen_era

    def pitcher_command_as_of(self, pitcher_id, as_of_date, season):
        return {"k_pct": 0.24, "bb_pct": 0.07}

    def historical_weather(self, lat, lon, game_date, as_of_date):
        return {"temp_f": self.temp_f}

    def league_averages_as_of(self, as_of_date, season):
        return {"league_ops": 0.750, "league_era": 4.30, "league_runs_per_game": 4.5}


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
