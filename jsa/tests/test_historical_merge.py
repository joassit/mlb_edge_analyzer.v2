"""`merge.py` fusiona N bases historicas separadas (una por temporada,
como las que produce cada corrida aislada de `jsa_historical_ingest.yml`
sin `JSA_HISTORICAL_DATABASE_URL` configurado) en una sola -- necesario
para que `validation.py`/`monte_carlo.py` puedan comparar varias
temporadas desde una unica base."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jsa.historical import db as historical_db
from jsa.historical import monte_carlo, pipeline, validation
from jsa.historical.merge import merge_databases
from jsa.tests.test_historical_point_in_time import FakeProvider


def _ingest_fake_season(tmp_path, season: int, game_pk_base: int, n_games: int = 6):
    hist_url = f"sqlite:///{tmp_path}/jsa_historical_{season}.db"
    prod_url = f"sqlite:///{tmp_path}/jsa_prod_{season}.db"
    games = [
        {"game_pk": game_pk_base + i, "season": season, "game_date": f"{season}-04-{10 + i:02d}",
         "home_team": "New York Yankees", "away_team": "Boston Red Sox", "home_team_id": 147, "away_team_id": 111,
         "home_pitcher_id": 1000 + i, "away_pitcher_id": 2000 + i, "is_double_header": False,
         "home_score": 5 if i % 2 == 0 else 2, "away_score": 3 if i % 2 == 0 else 6}
        for i in range(n_games)
    ]
    with patch("jsa.historical.pipeline.fetch_season_games", return_value=games):
        pipeline.run_season_ingestion(season, provider=FakeProvider(), historical_database_url=hist_url, registries_database_url=prod_url)
    return hist_url


@pytest.fixture()
def two_ingested_seasons(tmp_path):
    url_2022 = _ingest_fake_season(tmp_path, 2022, game_pk_base=950000)
    url_2023 = _ingest_fake_season(tmp_path, 2023, game_pk_base=960000)
    return url_2022, url_2023


def test_merge_databases_combines_row_counts(two_ingested_seasons, tmp_path):
    url_2022, url_2023 = two_ingested_seasons
    target_url = f"sqlite:///{tmp_path}/merged.db"

    counts = merge_databases([url_2022, url_2023], target_url)

    assert counts["historical_game"] == 12  # 6 + 6
    assert counts["historical_snapshot"] == 12
    assert counts["historical_report"] == 12
    assert counts["historical_season_run"] == 2


def test_merge_databases_preserves_both_seasons_independently(two_ingested_seasons, tmp_path):
    url_2022, url_2023 = two_ingested_seasons
    target_url = f"sqlite:///{tmp_path}/merged.db"
    merge_databases([url_2022, url_2023], target_url)

    target_engine = historical_db.get_engine(target_url)
    assert len(historical_db.games_for_season(target_engine, 2022)) == 6
    assert len(historical_db.games_for_season(target_engine, 2023)) == 6


def test_merge_databases_is_idempotent(two_ingested_seasons, tmp_path):
    url_2022, url_2023 = two_ingested_seasons
    target_url = f"sqlite:///{tmp_path}/merged.db"

    merge_databases([url_2022, url_2023], target_url)
    counts_second_run = merge_databases([url_2022, url_2023], target_url)

    # Segunda fusion sobre el mismo destino no debe duplicar filas --
    # insert_ignore_duplicates descarta los game_pk ya presentes.
    target_engine = historical_db.get_engine(target_url)
    assert len(historical_db.games_for_season(target_engine, 2022)) == 6
    assert len(historical_db.games_for_season(target_engine, 2023)) == 6
    assert counts_second_run["historical_game"] == 12  # filas LEIDAS de la fuente, no filas nuevas


def test_validation_and_monte_carlo_run_against_merged_database(two_ingested_seasons, tmp_path):
    url_2022, url_2023 = two_ingested_seasons
    target_url = f"sqlite:///{tmp_path}/merged.db"
    merge_databases([url_2022, url_2023], target_url)

    benchmark_2022 = validation.benchmark_season(2022, target_url)
    benchmark_2023 = validation.benchmark_season(2023, target_url)
    assert benchmark_2022["n_games_ingested"] == 6
    assert benchmark_2023["n_games_ingested"] == 6

    audit_2022 = monte_carlo.run_monte_carlo_audit(2022, target_url, n_simulations=20)
    assert audit_2022.n_games == 6
    assert audit_2022.n_simulations == 20
