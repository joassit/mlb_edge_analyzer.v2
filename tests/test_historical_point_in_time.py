"""
Pruebas de las protecciones anti-fuga de historical_engine/point_in_time_stats.py.

Usa un FakeProvider con datos controlados que incluyen entradas ANTES y
DESPUÉS de la fecha de corte -- si el motor alguna vez devolviera algo
calculado con datos "después", estas pruebas lo detectan de forma
determinista, sin depender de red ni de la MLB Stats API real.
"""

import pytest

from historical_engine.point_in_time_provider import HistoricalStatsProvider
from historical_engine.point_in_time_stats import (
    LookAheadBiasError,
    reconstruct_team_pitcher_features,
    reconstruct_game_features,
    default_as_of_date,
    _assert_no_lookahead,
)


class FakeProvider(HistoricalStatsProvider):
    """
    Simula un pitcher/equipo cuyas estadísticas "verdaderas" cambian antes
    y después de una fecha de corte conocida (2026-06-01) -- si el motor
    pide datos "as of" 2026-05-15, debe recibir los valores PRE-corte
    (era=3.00), nunca los POST-corte (era=99.00, un valor centinela
    imposible que ninguna prueba puede confundir con un resultado real).
    """
    CUTOFF = "2026-06-01"

    def __init__(self):
        self.calls = []  # registra cada as_of_date que se le pidió, para poder auditar

    def pitcher_era_ip_as_of(self, pitcher_id, as_of_date, season):
        self.calls.append(("era", as_of_date))
        if as_of_date <= self.CUTOFF:
            return (3.00, 50.0)  # "verdad" pre-corte
        return (99.00, 999.0)  # centinela post-corte -- nunca debe verse en una prueba con as_of < CUTOFF

    def team_ops_as_of(self, team_id, as_of_date, season):
        self.calls.append(("ops", as_of_date))
        if as_of_date <= self.CUTOFF:
            return (0.700, 4200)  # "verdad" pre-corte -- PA real, no aproximado
        return (0.999, 100)  # centinela post-corte

    def bullpen_era_as_of(self, team_id, as_of_date, season):
        self.calls.append(("bullpen", as_of_date))
        if as_of_date <= self.CUTOFF:
            return 4.00
        return 99.0

    def pitcher_command_as_of(self, pitcher_id, as_of_date, season):
        self.calls.append(("command", as_of_date))
        if as_of_date <= self.CUTOFF:
            return {"k_pct": 0.22, "bb_pct": 0.07}
        return {"k_pct": 0.99, "bb_pct": 0.99}

    def pitcher_rest_as_of(self, pitcher_id, as_of_date, season):
        self.calls.append(("rest", as_of_date))
        if as_of_date <= self.CUTOFF:
            return {"days_rest": 4, "last_outing_pitches": 90}
        return {"days_rest": -1, "last_outing_pitches": -1}

    def historical_weather(self, lat, lon, game_date, as_of_date):
        self.calls.append(("weather", game_date, as_of_date))
        return {"temp_f": 75.0}

    def league_averages_as_of(self, as_of_date, season):
        self.calls.append(("league", as_of_date))
        if as_of_date <= self.CUTOFF:
            return {"league_ops": 0.720, "league_era": 4.10, "league_runs_per_game": 4.5}
        return {"league_ops": 0.999, "league_era": 99.0, "league_runs_per_game": 99.0}


def test_reconstructs_pre_cutoff_values_only():
    provider = FakeProvider()
    features = reconstruct_team_pitcher_features(
        team_id=111, pitcher_id=999, as_of_date="2026-05-15", game_date="2026-05-16",
        season=2026, provider=provider,
    )
    assert features.era == 3.00
    assert features.innings_pitched == 50.0
    assert features.ops == 0.700
    assert features.team_pa == 4200
    assert features.bullpen_era == 4.00
    assert features.k_pct == 0.22
    assert features.days_rest == 4


def test_never_returns_post_cutoff_sentinel_values_even_when_cutoff_is_close():
    # as_of_date es apenas 1 día antes de un game_date que cae DESPUÉS del
    # "cambio de verdad" del FakeProvider (2026-06-01) -- si hubiera
    # cualquier desliz de un día en la lógica de corte, este test lo
    # atraparía viendo aparecer los valores centinela (99.0, 999, -1).
    provider = FakeProvider()
    features = reconstruct_team_pitcher_features(
        team_id=111, pitcher_id=999, as_of_date="2026-06-01", game_date="2026-06-02",
        season=2026, provider=provider,
    )
    assert features.era == 3.00  # 2026-06-01 <= CUTOFF -> todavía pre-corte
    assert features.era != 99.00
    assert features.bullpen_era != 99.0
    assert features.days_rest != -1


def test_raises_look_ahead_bias_error_when_as_of_date_equals_game_date():
    provider = FakeProvider()
    with pytest.raises(LookAheadBiasError):
        reconstruct_team_pitcher_features(
            team_id=111, pitcher_id=999, as_of_date="2026-05-16", game_date="2026-05-16",
            season=2026, provider=provider,
        )


def test_raises_look_ahead_bias_error_when_as_of_date_is_after_game_date():
    provider = FakeProvider()
    with pytest.raises(LookAheadBiasError):
        reconstruct_team_pitcher_features(
            team_id=111, pitcher_id=999, as_of_date="2026-05-20", game_date="2026-05-16",
            season=2026, provider=provider,
        )
    # La protección debe dispararse ANTES de llamar al proveedor -- cero
    # llamadas de red/datos deben ocurrir si la fecha ya es inválida.
    assert provider.calls == []


def test_assert_no_lookahead_accepts_strictly_earlier_date():
    _assert_no_lookahead("2026-05-15", "2026-05-16")  # no debe lanzar


def test_reconstruct_game_features_covers_both_sides_and_park_and_weather():
    provider = FakeProvider()
    game = {
        "game_date": "2026-05-16",
        "away_team_id": 111, "home_team_id": 147,  # Fenway / Yankee Stadium (data/park_factors.py)
        "away_pitcher_id": 1001, "home_pitcher_id": 1002,
    }
    result = reconstruct_game_features(game, as_of_date="2026-05-15", season=2026, provider=provider)

    assert result["as_of_date"] == "2026-05-15"
    assert result["away_era"] == 3.00
    assert result["home_era"] == 3.00
    assert result["away_innings_pitched"] == 50.0
    assert result["home_innings_pitched"] == 50.0
    assert result["away_team_pa"] == 4200
    assert result["home_team_pa"] == 4200
    assert result["park_name"] == "Yankee Stadium"
    assert result["park_factor"] == 1.05
    assert result["temp_f"] == 75.0
    assert result["league_ops"] == 0.720
    assert result["league_era"] == 4.10


def test_reconstruct_game_features_raises_on_lookahead_before_touching_provider():
    provider = FakeProvider()
    game = {"game_date": "2026-05-16", "away_team_id": 111, "home_team_id": 147,
            "away_pitcher_id": 1001, "home_pitcher_id": 1002}
    with pytest.raises(LookAheadBiasError):
        reconstruct_game_features(game, as_of_date="2026-05-16", season=2026, provider=provider)
    assert provider.calls == []


def test_missing_pitcher_id_returns_none_fields_without_calling_provider_for_pitcher_stats():
    # Survivorship/completeness: un pitcher no confirmado (None) no debe
    # inventarse ni pedirse -- solo team-level (OPS/bullpen) se reconstruye.
    provider = FakeProvider()
    features = reconstruct_team_pitcher_features(
        team_id=111, pitcher_id=None, as_of_date="2026-05-15", game_date="2026-05-16",
        season=2026, provider=provider,
    )
    assert features.era is None
    assert features.k_pct is None
    assert features.days_rest is None
    assert ("era", "2026-05-15") not in provider.calls
    assert features.ops == 0.700  # el lado de equipo sigue reconstruyéndose
    assert features.team_pa == 4200


def test_default_as_of_date_is_one_day_before_game_date():
    assert default_as_of_date("2026-05-16") == "2026-05-15"
