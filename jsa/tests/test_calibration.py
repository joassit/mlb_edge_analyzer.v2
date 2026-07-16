"""`historical/calibration.py` -- ajuste y validacion (leave-one-season-out)
de una curva de calibracion isotonica de `evidence_score_raw`. Datos
sinteticos deterministicos sobre SQLite real basado en archivo (nunca
`:memory:` -- `fit_and_validate()` abre su PROPIO engine desde la URL,
que con `:memory:` seria una base vacia distinta a la usada para
sembrar; mismo criterio que `test_historical_pipeline.py::isolated_dbs`).
Nunca red, nunca Postgres real."""

from __future__ import annotations

import datetime

import pytest

from jsa.domain.models import CalibrationRegistryEntry
from jsa.historical import calibration
from jsa.historical import db as historical_db
from jsa.registries import db as registries_db

MIN_GAMES = calibration.MIN_GAMES_PER_SEASON


def _seed_season(engine, season: int, n_games: int, score_fn):
    """Siembra `n_games` juegos+reportes sinteticos para una temporada --
    `score_fn(i) -> (evidence_score_raw, home_win)` controla la relacion
    determinista entre score y resultado."""
    historical_db.init_historical_storage(engine)
    for i in range(n_games):
        game_pk = season * 100000 + i
        evidence_score_raw, home_win = score_fn(i)
        game_date = datetime.date(season, 4, 1) + datetime.timedelta(days=i % 150)
        historical_db.upsert_game(
            engine, season=season, game_pk=game_pk, game_date=game_date,
            home_team="Home", away_team="Away", home_team_id=1, away_team_id=2,
            home_pitcher_id=100, away_pitcher_id=200, is_double_header=0,
        )
        home_score, away_score = (5, 3) if home_win else (3, 5)
        historical_db.update_game_result(engine, game_pk, home_score, away_score)
        historical_db.persist_historical_report(
            engine, run_id=f"run-{season}", season=season, game_pk=game_pk, game_date=game_date,
            report_payload={"manifest_status": "valid", "evidence_score_raw": evidence_score_raw},
        )


def _monotonic_score_fn(i):
    # score va de -2.0 a 2.0 a lo largo de los juegos; home gana siempre
    # que el score sea positivo -- relacion perfectamente monotona, sin
    # ruido, para poder verificar la FORMA de la curva ajustada.
    score = -2.0 + (i % 40) * (4.0 / 39)
    return score, score > 0


@pytest.fixture()
def hist_url(tmp_path):
    return f"sqlite:///{tmp_path}/jsa_calibration_test.db"


def test_season_evidence_pairs_reads_evidence_score_raw(hist_url):
    engine = historical_db.get_engine(hist_url)
    _seed_season(engine, 2022, 60, _monotonic_score_fn)

    pairs = calibration.season_evidence_pairs(engine, 2022)
    assert len(pairs) == 60
    high = [y for x, y in pairs if x > 0]
    low = [y for x, y in pairs if x < 0]
    assert sum(high) / len(high) > sum(low) / len(low)


def test_season_evidence_pairs_skips_games_without_result(hist_url):
    engine = historical_db.get_engine(hist_url)
    historical_db.init_historical_storage(engine)
    historical_db.upsert_game(
        engine, season=2022, game_pk=1, game_date=datetime.date(2022, 4, 1),
        home_team="Home", away_team="Away", home_team_id=1, away_team_id=2,
        home_pitcher_id=100, away_pitcher_id=200, is_double_header=0,
    )
    # sin update_game_result -- winner queda None, el juego se descarta.
    historical_db.persist_historical_report(
        engine, run_id="run", season=2022, game_pk=1, game_date=datetime.date(2022, 4, 1),
        report_payload={"manifest_status": "valid", "evidence_score_raw": 1.0},
    )
    assert calibration.season_evidence_pairs(engine, 2022) == []


def test_fit_and_validate_rejected_when_no_season_has_enough_games(hist_url):
    engine = historical_db.get_engine(hist_url)
    _seed_season(engine, 2022, MIN_GAMES - 1, _monotonic_score_fn)

    result = calibration.fit_and_validate([2022], hist_url)
    assert result["status"] == "rejected_insufficient_data"
    assert result["loso_seasons_validated"] == []


def test_fit_and_validate_under_validation_when_fewer_than_3_seasons_pass(hist_url):
    engine = historical_db.get_engine(hist_url)
    _seed_season(engine, 2022, MIN_GAMES, _monotonic_score_fn)
    _seed_season(engine, 2023, MIN_GAMES, _monotonic_score_fn)

    result = calibration.fit_and_validate([2022, 2023], hist_url)
    assert result["status"] == "under_validation"
    assert len(result["loso_seasons_validated"]) == 2


def test_fit_and_validate_validated_with_3plus_seasons(hist_url):
    engine = historical_db.get_engine(hist_url)
    for season in (2022, 2023, 2024):
        _seed_season(engine, season, MIN_GAMES, _monotonic_score_fn)

    result = calibration.fit_and_validate([2022, 2023, 2024], hist_url)
    assert result["status"] == "validated"
    assert sorted(result["loso_seasons_validated"]) == [2022, 2023, 2024]
    assert result["loso_n_games"] == MIN_GAMES * 3
    assert result["n_games_fitted"] == MIN_GAMES * 3


def test_fit_and_validate_recovers_monotonic_relationship(hist_url):
    engine = historical_db.get_engine(hist_url)
    for season in (2022, 2023, 2024):
        _seed_season(engine, season, MIN_GAMES, _monotonic_score_fn)

    result = calibration.fit_and_validate([2022, 2023, 2024], hist_url)
    # La curva de produccion debe ser monotona no decreciente (propiedad
    # estructural de isotonic regression) y debe favorecer scores altos.
    y_knots = result["y_knots"]
    assert y_knots == sorted(y_knots)
    assert y_knots[0] < y_knots[-1]
    # Con una relacion perfecta y sin ruido, la calibracion LOSO deberia
    # ser practicamente perfecta (Brier muy bajo).
    assert result["loso_brier"] < 0.05


def test_fit_and_validate_partial_season_excluded_from_loso_but_included_in_production_fit(hist_url):
    engine = historical_db.get_engine(hist_url)
    for season in (2022, 2023, 2024):
        _seed_season(engine, season, MIN_GAMES, _monotonic_score_fn)
    _seed_season(engine, 2025, MIN_GAMES - 10, _monotonic_score_fn)  # temporada parcial (en curso)

    result = calibration.fit_and_validate([2022, 2023, 2024, 2025], hist_url)
    assert result["status"] == "validated"  # las 3 completas ya alcanzan
    assert 2025 not in result["loso_seasons_validated"]
    assert result["n_games_fitted"] == MIN_GAMES * 3 + (MIN_GAMES - 10)  # 2025 SI cuenta para la curva final


def test_fit_and_validate_result_round_trips_through_calibration_registry(hist_url, tmp_path):
    """El dict que devuelve `fit_and_validate()` debe poder persistirse tal
    cual en `calibration_registry` y volver a leerse como
    `CalibrationRegistryEntry` valido -- el mismo contrato que
    `historical/cli.py::calibrate` usa en produccion."""
    engine = historical_db.get_engine(hist_url)
    for season in (2022, 2023, 2024):
        _seed_season(engine, season, MIN_GAMES, _monotonic_score_fn)
    result = calibration.fit_and_validate([2022, 2023, 2024], hist_url)

    registries_url = f"sqlite:///{tmp_path}/jsa_registries_test.db"
    registries_engine = registries_db.get_engine(registries_url)
    registries_db.init_registries(registries_engine)
    registries_db.append(
        registries_engine, registries_db.calibration_registry,
        calibration_id="calibration-evidence_score_raw-v1", market="moneyline_home", source_field="evidence_score_raw",
        method="isotonic_regression", x_knots=result["x_knots"], y_knots=result["y_knots"],
        x_min=result["x_min"], x_max=result["x_max"], n_games_fitted=result["n_games_fitted"],
        seasons_used=result["seasons_used"], loso_seasons_validated=result["loso_seasons_validated"],
        loso_n_games=result["loso_n_games"], loso_brier=result["loso_brier"], loso_log_loss=result["loso_log_loss"],
        loso_accuracy=result["loso_accuracy"], loso_ece=result["loso_ece"], loso_mce=result["loso_mce"],
        status=result["status"], date="2026-07-16",
    )

    latest = registries_db.latest_by_id(registries_engine, registries_db.calibration_registry, "calibration_id")
    row = latest["calibration-evidence_score_raw-v1"]
    entry = CalibrationRegistryEntry(**row)
    assert entry.status == "validated"
    assert entry.market == "moneyline_home"
    assert len(entry.x_knots) == len(entry.y_knots)
