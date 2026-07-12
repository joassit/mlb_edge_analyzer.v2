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


def test_ingest_season_supports_2022_with_lockout_adjusted_start(tmp_path, monkeypatch):
    """2022 arrancó tarde por el lockout de la MLB -- Opening Day real fue
    el 7 de abril, no fines de marzo como las otras temporadas. Confirma
    que ingest_season(2022, ...) no lanza (SEASON_DATE_RANGES lo cubre) y
    que efectivamente arranca la ingesta en la fecha de inicio de esa
    temporada, no antes."""
    Session = _fresh_session(tmp_path, monkeypatch, "ingestion_2022")

    calls = []

    def fake_get_schedule(d):
        calls.append(d)
        return []

    monkeypatch.setattr(ingestion, "get_schedule", fake_get_schedule)
    monkeypatch.setattr(ingestion, "get_game_result", lambda game_pk: None)

    summary = ingestion.ingest_season(2022, run_id=1)

    assert summary["n_errors"] == 0
    assert min(calls) == date(2022, 4, 7)
    assert max(calls) == date(2022, 10, 5)


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
    assert summary["n_postponed_reconciled"] == 0


def test_ingest_date_range_reconciles_postponed_game_without_changing_game_date(tmp_path, monkeypatch):
    """Regresión del hallazgo de auditoría: un juego pospuesto (ingerido
    primero sin resultado) que reaparece en el calendario de la fecha de
    reanudación, ya Final, debe actualizar winner/scores en la MISMA fila
    -- nunca crear una segunda fila, y nunca tocar game_date (as_of_date
    se calcula desde la fecha ORIGINAL programada, no la de reanudación:
    correrla movería el corte point-in-time)."""
    Session = _fresh_session(tmp_path, monkeypatch, "ingestion_reconcile")

    def _game_with_state(game_pk, abstract_state, game_date_official):
        g = _game(game_pk=game_pk)
        g["abstract_state"] = abstract_state
        g["status"] = abstract_state
        g["game_date_official"] = game_date_official
        return g

    # Día original: el juego se pospone antes de jugarse -- MLB API sigue
    # reportando game_date_official = la fecha originalmente programada.
    schedule_day1 = {date(2024, 6, 10): [_game_with_state(746799, "Postponed", "2024-06-10")]}
    monkeypatch.setattr(ingestion, "get_schedule", lambda d: schedule_day1.get(d, []))
    monkeypatch.setattr(ingestion, "get_game_result", lambda game_pk: None)

    ingestion.ingest_date_range(date(2024, 6, 10), date(2024, 6, 10), run_id=1, season=2024)

    session = Session()
    row = session.query(historical_db.HistoricalGame).filter_by(run_id=1, game_pk=746799).one()
    assert row.winner is None
    assert row.game_date == "2024-06-10"
    session.close()

    # Día de reanudación: mismo game_pk, ahora Final, pero la API sigue
    # reportando game_date_official = la fecha ORIGINAL (2024-06-10), no
    # la de reanudación (2024-06-12) -- así lo hace la MLB Stats API real.
    schedule_day2 = {date(2024, 6, 12): [_game_with_state(746799, "Final", "2024-06-10")]}
    monkeypatch.setattr(ingestion, "get_schedule", lambda d: schedule_day2.get(d, []))
    monkeypatch.setattr(ingestion, "get_game_result",
                         lambda game_pk: {"home_score": 6, "away_score": 3, "winner": "home", "total_runs": 9})

    summary = ingestion.ingest_date_range(date(2024, 6, 12), date(2024, 6, 12), run_id=1, season=2024)

    assert summary["n_duplicate_game_pk_skipped"] == 1
    assert summary["n_postponed_reconciled"] == 1

    session = Session()
    rows = session.query(historical_db.HistoricalGame).filter_by(run_id=1, game_pk=746799).all()
    session.close()
    assert len(rows) == 1  # nunca una segunda fila para el mismo (game_pk, run_id)
    row = rows[0]
    assert row.game_date == "2024-06-10"  # el game_date ORIGINAL nunca se toca
    assert row.winner == "home"
    assert row.home_score == 6 and row.away_score == 3 and row.total_runs == 9
    assert row.status == "Final"


def test_ingest_date_range_does_not_re_fetch_result_when_already_reconciled(tmp_path, monkeypatch):
    """Si la fila existente YA tiene resultado (no es un pospuesto sin
    resolver), una reaparición del mismo game_pk no debe volver a llamar
    get_game_result() -- protege contra pisar un resultado ya correcto con
    una llamada redundante a la API."""
    Session = _fresh_session(tmp_path, monkeypatch, "ingestion_no_reconcile_needed")

    schedule_by_date = {
        date(2024, 5, 14): [_game(game_pk=746799)],  # Final desde el día 1
        date(2024, 5, 15): [_game(game_pk=746799)],  # reaparece, también Final
    }
    monkeypatch.setattr(ingestion, "get_schedule", lambda d: schedule_by_date.get(d, []))
    call_count = {"n": 0}

    def fake_get_game_result(game_pk):
        call_count["n"] += 1
        return {"home_score": 5, "away_score": 2, "winner": "home", "total_runs": 7}

    monkeypatch.setattr(ingestion, "get_game_result", fake_get_game_result)

    summary = ingestion.ingest_date_range(date(2024, 5, 14), date(2024, 5, 15), run_id=1, season=2024)

    assert summary["n_postponed_reconciled"] == 0
    assert call_count["n"] == 1  # solo el día 1 -- el día 2 ni siquiera lo intenta
