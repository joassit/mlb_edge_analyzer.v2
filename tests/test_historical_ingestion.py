"""
Pruebas de historical_engine/ingestion.py -- monkeypatchea get_schedule()/
get_game_result() (cero llamadas de red real) para ejercitar el guard de
idempotencia contra game_pk repetido entre días.

Regresión del bug real encontrado corriendo 2024-05 completo contra la
MLB Stats API: un juego suspendido/reprogramado apareció en el calendario
de más de un día dentro del mismo rango, y la ausencia de este guard
tumbaba ingest_date_range() con un UNIQUE constraint failed a mitad de la
corrida (historical_game.game_pk, historical_game.run_id).
"""

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import historical_engine.db as historical_db
import historical_engine.ingestion as ingestion


def _fresh_session(tmp_path, monkeypatch, name: str):
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db")
    historical_db.HistoricalBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(historical_db, "SessionLocal", Session)
    monkeypatch.setattr(ingestion, "SessionLocal", Session)
    return Session


def _game(game_pk=746799, away_team="Washington Nationals", home_team="Chicago White Sox"):
    return {
        "game_pk": game_pk, "abstract_state": "Final",
        "away_team": away_team, "home_team": home_team,
        "away_team_id": 120, "home_team_id": 145,
        "away_pitcher_id": 680730, "home_pitcher_id": 607200,
        "away_pitcher_name": "Mitchell Parker", "home_pitcher_name": "Erick Fedde",
        "status": "Final", "game_date_official": None,
    }


def test_ingest_date_range_skips_game_pk_seen_on_a_previous_day(tmp_path, monkeypatch):
    """Reproduce el bug real: la misma game_pk aparece en el calendario de
    DOS días distintos dentro del rango (juego suspendido/reprogramado) --
    debe saltarse la segunda vez, nunca crashear con IntegrityError."""
    Session = _fresh_session(tmp_path, monkeypatch, "ingestion_dup")

    schedule_by_date = {
        date(2024, 5, 14): [_game(game_pk=746799)],
        date(2024, 5, 15): [_game(game_pk=746799)],  # misma game_pk, otro día -- el bug real
    }

    def fake_get_schedule(d):
        return schedule_by_date.get(d, [])

    def fake_get_game_result(game_pk):
        return {"home_score": 5, "away_score": 2, "winner": "home", "total_runs": 7}

    monkeypatch.setattr(ingestion, "get_schedule", fake_get_schedule)
    monkeypatch.setattr(ingestion, "get_game_result", fake_get_game_result)

    summary = ingestion.ingest_date_range(date(2024, 5, 14), date(2024, 5, 15), run_id=1, season=2024)

    assert summary["n_games"] == 1
    assert summary["n_duplicate_game_pk_skipped"] == 1
    assert summary["n_errors"] == 0

    session = Session()
    rows = session.query(historical_db.HistoricalGame).filter_by(run_id=1, game_pk=746799).all()
    session.close()
    assert len(rows) == 1  # nunca dos filas para el mismo (game_pk, run_id)


def test_ingest_date_range_skips_game_pk_already_ingested_in_a_prior_call(tmp_path, monkeypatch):
    """Mismo guard, pero contra un game_pk ya presente en la DB de un
    llamado anterior a ingest_date_range() para el mismo run_id (no solo
    duplicado dentro de la misma corrida en memoria)."""
    Session = _fresh_session(tmp_path, monkeypatch, "ingestion_dup_prior_call")

    session = Session()
    session.add(historical_db.HistoricalGame(
        run_id=1, game_pk=746799, game_date="2024-05-14", season_year=2024,
        away_team="Washington Nationals", home_team="Chicago White Sox",
        away_team_id=120, home_team_id=145,
    ))
    session.commit()
    session.close()

    def fake_get_schedule(d):
        return [_game(game_pk=746799)] if d == date(2024, 5, 15) else []

    monkeypatch.setattr(ingestion, "get_schedule", fake_get_schedule)
    monkeypatch.setattr(ingestion, "get_game_result", lambda game_pk: None)

    summary = ingestion.ingest_date_range(date(2024, 5, 15), date(2024, 5, 15), run_id=1, season=2024)

    assert summary["n_games"] == 0
    assert summary["n_duplicate_game_pk_skipped"] == 1

    session = Session()
    assert session.query(historical_db.HistoricalGame).filter_by(run_id=1, game_pk=746799).count() == 1
    session.close()


def test_ingest_date_range_still_ingests_distinct_game_pks_normally(tmp_path, monkeypatch):
    Session = _fresh_session(tmp_path, monkeypatch, "ingestion_normal")

    schedule_by_date = {
        date(2024, 5, 14): [_game(game_pk=1001), _game(game_pk=1002)],
        date(2024, 5, 15): [_game(game_pk=1003)],
    }
    monkeypatch.setattr(ingestion, "get_schedule", lambda d: schedule_by_date.get(d, []))
    monkeypatch.setattr(ingestion, "get_game_result",
                         lambda game_pk: {"home_score": 5, "away_score": 2, "winner": "home", "total_runs": 7})

    summary = ingestion.ingest_date_range(date(2024, 5, 14), date(2024, 5, 15), run_id=1, season=2024)

    assert summary["n_games"] == 3
    assert summary["n_duplicate_game_pk_skipped"] == 0
    assert summary["n_final"] == 3
