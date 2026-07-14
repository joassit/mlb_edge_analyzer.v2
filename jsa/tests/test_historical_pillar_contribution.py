"""Lado con I/O de PillarContributionAnalyzer -- `jsa/historical/
pillar_contribution.py::analyze_season_pillar_contribution()` sobre una
temporada real ingerida (mismo patron de fixture que
test_historical_validation.py/test_historical_merge.py)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jsa.domain.models import SEVEN_PILLARS
from jsa.historical import pipeline
from jsa.historical.pillar_contribution import analyze_season_pillar_contribution
from jsa.tests.test_historical_point_in_time import FakeProvider


@pytest.fixture()
def ingested_season(tmp_path):
    hist_url = f"sqlite:///{tmp_path}/jsa_historical_test.db"
    prod_url = f"sqlite:///{tmp_path}/jsa_prod_test.db"
    games = [
        {"game_pk": 970000 + i, "season": 2022, "game_date": f"2022-05-{10 + i:02d}",
         "home_team": "New York Yankees", "away_team": "Boston Red Sox", "home_team_id": 147, "away_team_id": 111,
         "home_pitcher_id": 1000 + i, "away_pitcher_id": 2000 + i, "is_double_header": False,
         "home_score": 5 if i % 2 == 0 else 2, "away_score": 3 if i % 2 == 0 else 6}
        for i in range(8)
    ]
    with patch("jsa.historical.pipeline.fetch_season_games", return_value=games):
        pipeline.run_season_ingestion(2022, provider=FakeProvider(), historical_database_url=hist_url, registries_database_url=prod_url)
    return hist_url


def test_analyze_season_pillar_contribution_covers_all_ingested_games(ingested_season):
    report = analyze_season_pillar_contribution(2022, ingested_season)
    assert report.n_games == 8
    assert set(report.stats_by_pillar.keys()) == set(SEVEN_PILLARS)
    # FakeProvider (fixture minima, sin datos reales) produce snapshots
    # sin señal en ningun pilar -- most_dominant_pillar==None es el
    # resultado correcto en ese caso degenerado (ver test_tied_pillars_*
    # en test_pillar_contribution_analyzer.py), no un pilar arbitrario.
    assert report.most_dominant_pillar is None or report.most_dominant_pillar in SEVEN_PILLARS


def test_analyze_season_pillar_contribution_percentages_are_bounded(ingested_season):
    report = analyze_season_pillar_contribution(2022, ingested_season)
    for stats in report.stats_by_pillar.values():
        assert 0.0 <= stats.mean_percentage_contribution <= 1.0
        assert 0.0 <= stats.dominance_warning_rate <= 1.0
        assert 0.0 <= stats.top_contributor_rate <= 1.0
        # cada juego tiene exactamente un pilar argmax -- las 7 tasas suman 1.
    assert sum(s.top_contributor_rate for s in report.stats_by_pillar.values()) == pytest.approx(1.0)


def test_analyze_season_pillar_contribution_empty_season_returns_zero_games(tmp_path):
    empty_url = f"sqlite:///{tmp_path}/empty_historical.db"
    report = analyze_season_pillar_contribution(2099, empty_url)
    assert report.n_games == 0
    assert report.stats_by_pillar == {}
