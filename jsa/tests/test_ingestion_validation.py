"""`historical/ingestion_validation.py::validate_season_ingestion()` --
validaciones estructurales post-ingesta (cobertura de snapshots,
cobertura de los 8 campos nuevos de Trend, consistencia de fechas entre
`historical_game`/`historical_snapshot`). Nunca red, nunca Postgres real."""

from __future__ import annotations

import datetime

import pytest

from jsa.historical import db as historical_db
from jsa.historical.ingestion_validation import (
    MIN_FIELD_COVERAGE_PCT,
    ROLLING_TREND_FIELDS,
    validate_season_ingestion,
)


@pytest.fixture()
def hist_url(tmp_path):
    return f"sqlite:///{tmp_path}/jsa_ingestion_validation_test.db"


def _full_payload(**overrides) -> dict:
    payload = {field: 0.5 for field in ROLLING_TREND_FIELDS}
    payload.update(overrides)
    return payload


def _seed_game_and_snapshot(engine, season, game_pk, game_date, *, with_result=True, payload=None, snapshot_date=None):
    historical_db.upsert_game(
        engine, season=season, game_pk=game_pk, game_date=game_date,
        home_team="H", away_team="A", home_team_id=1, away_team_id=2,
        home_pitcher_id=1, away_pitcher_id=2, is_double_header=0,
    )
    if with_result:
        historical_db.update_game_result(engine, game_pk, 5, 3)
    historical_db.persist_historical_snapshot(
        engine, season=season, game_pk=game_pk, game_date=snapshot_date or game_date,
        snapshot_hash=f"hash-{game_pk}", snapshot_payload=payload or _full_payload(),
    )


def test_validate_season_ingestion_ok_with_full_coverage(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    for i in range(20):
        _seed_game_and_snapshot(engine, 2022, 2022_000 + i, datetime.date(2022, 4, 1 + i % 28))

    result = validate_season_ingestion(hist_url, 2022)
    assert result["status"] == "ok"
    assert result["issues"] == []
    assert result["n_games"] == 20
    assert result["n_snapshots"] == 20
    assert result["snapshot_coverage_pct"] == 1.0
    for field in ROLLING_TREND_FIELDS:
        assert result["rolling_trend_field_coverage_pct"][field] == 1.0


def test_validate_season_ingestion_fails_on_low_snapshot_coverage(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    # 20 juegos con resultado, solo 5 con snapshot -- ingesta claramente incompleta.
    for i in range(20):
        historical_db.upsert_game(
            engine, season=2022, game_pk=2022_000 + i, game_date=datetime.date(2022, 4, 1 + i % 28),
            home_team="H", away_team="A", home_team_id=1, away_team_id=2,
            home_pitcher_id=1, away_pitcher_id=2, is_double_header=0,
        )
        historical_db.update_game_result(engine, 2022_000 + i, 5, 3)
    for i in range(5):
        historical_db.persist_historical_snapshot(
            engine, season=2022, game_pk=2022_000 + i, game_date=datetime.date(2022, 4, 1 + i % 28),
            snapshot_hash=f"hash-{i}", snapshot_payload=_full_payload(),
        )

    result = validate_season_ingestion(hist_url, 2022)
    assert result["status"] == "failed"
    assert any("cobertura de snapshots" in issue for issue in result["issues"])


def test_validate_season_ingestion_fails_on_low_field_coverage(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    # Primeros dias de temporada -- ESPERADO que la ventana de 7/14 dias
    # este vacia (no null-porque-esta-roto, sino porque todavia no
    # jugaron suficientes partidos) -- pero si TODOS los juegos de la
    # temporada tienen el campo en None, eso ya no es un inicio de
    # temporada legitimo, es una API rota.
    for i in range(20):
        _seed_game_and_snapshot(
            engine, 2022, 2022_000 + i, datetime.date(2022, 4, 1 + i % 28),
            payload=_full_payload(home_team_ops_rolling_7d=None, away_team_ops_rolling_7d=None),
        )

    result = validate_season_ingestion(hist_url, 2022)
    assert result["status"] == "failed"
    assert any("home_team_ops_rolling_7d" in issue for issue in result["issues"])
    assert result["rolling_trend_field_coverage_pct"]["home_team_ops_rolling_7d"] == 0.0


def test_validate_season_ingestion_tolerates_partial_early_season_nulls(hist_url):
    """No debe fallar solo porque ALGUNOS juegos tempranos de temporada no
    tengan ventana completa -- el umbral es sobre el TOTAL, no exige 100%."""
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    n = 20
    n_null = int(n * (1 - MIN_FIELD_COVERAGE_PCT)) - 1  # deliberadamente por debajo del umbral de falla
    for i in range(n):
        payload = _full_payload()
        if i < n_null:
            payload["home_team_ops_rolling_7d"] = None
        _seed_game_and_snapshot(engine, 2022, 2022_000 + i, datetime.date(2022, 4, 1 + i % 28), payload=payload)

    result = validate_season_ingestion(hist_url, 2022)
    assert result["status"] == "ok"


def test_validate_season_ingestion_fails_on_game_date_mismatch(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    for i in range(20):
        _seed_game_and_snapshot(engine, 2022, 2022_000 + i, datetime.date(2022, 4, 1 + i % 28))
    # Un snapshot con game_date distinto al juego real que dice representar.
    historical_db.upsert_game(
        engine, season=2022, game_pk=999999, game_date=datetime.date(2022, 5, 1),
        home_team="H", away_team="A", home_team_id=1, away_team_id=2,
        home_pitcher_id=1, away_pitcher_id=2, is_double_header=0,
    )
    historical_db.update_game_result(engine, 999999, 5, 3)
    historical_db.persist_historical_snapshot(
        engine, season=2022, game_pk=999999, game_date=datetime.date(2022, 4, 15),  # distinto a game_date real (5/1)
        snapshot_hash="hash-999999", snapshot_payload=_full_payload(),
    )

    result = validate_season_ingestion(hist_url, 2022)
    assert result["status"] == "failed"
    assert any("game_date distinto" in issue for issue in result["issues"])


def test_validate_season_ingestion_no_games_is_ok_not_failed(hist_url):
    """Una temporada sin juegos (o sin resultados todavia) no es un fallo
    -- simplemente no hay nada que validar todavia."""
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    result = validate_season_ingestion(hist_url, 2099)
    assert result["status"] == "ok"
    assert result["n_games"] == 0
