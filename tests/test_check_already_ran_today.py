"""
Pruebas de scripts/check_already_ran_today.py -- la guarda de
idempotencia del cron de respaldo (daily_pipeline.yml). Nunca toca un
mlb_edge.db real ni la MLB Stats API real (get_schedule mockeado).
"""

from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.database as database
import scripts.check_already_ran_today as check_script


def _isolated_db(tmp_path, monkeypatch):
    """No llama a init_db() -- a propósito, para que
    test_already_ran_today_is_false_on_a_fresh_db_without_any_tables
    pueda probar que already_ran_today() crea las tablas ella misma.
    Los demás tests llaman a database.init_db() explícitamente antes de
    usar save_analysis()."""
    temp_engine = create_engine(f"sqlite:///{tmp_path}/check_test.db")
    monkeypatch.setattr(database, "engine", temp_engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=temp_engine))
    return database


def _eligible_game(game_pk=1, abstract_state="Preview", away_pitcher_id=10, home_pitcher_id=20):
    return {
        "game_pk": game_pk, "abstract_state": abstract_state,
        "away_pitcher_id": away_pitcher_id, "home_pitcher_id": home_pitcher_id,
    }


def _mock_schedule(monkeypatch, games):
    monkeypatch.setattr(check_script, "get_schedule", lambda target_date=None: games)


def test_already_ran_today_is_false_on_a_fresh_db_without_any_tables(tmp_path, monkeypatch):
    # Caché fría / primer uso: ni siquiera existe la tabla game_analysis
    # todavía -- init_db() dentro de already_ran_today() debe crearla,
    # no reventar con "no such table". Hay 1 juego elegible hoy y 0
    # filas guardadas -- incompleto, debe reintentar.
    _isolated_db(tmp_path, monkeypatch)
    _mock_schedule(monkeypatch, [_eligible_game()])
    assert check_script.already_ran_today() is False


def test_already_ran_today_is_true_when_all_eligible_games_are_saved(tmp_path, monkeypatch):
    db_mod = _isolated_db(tmp_path, monkeypatch)
    db_mod.init_db()
    today = date.today().strftime("%Y-%m-%d")
    db_mod.save_analysis({
        "game_pk": 1, "game_date": today, "away_team": "A", "home_team": "B",
        "away_model_prob": 0.6, "home_model_prob": 0.4,
    })
    _mock_schedule(monkeypatch, [_eligible_game(game_pk=1)])

    assert check_script.already_ran_today() is True


def test_already_ran_today_ignores_rows_from_other_dates(tmp_path, monkeypatch):
    db_mod = _isolated_db(tmp_path, monkeypatch)
    db_mod.init_db()
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    db_mod.save_analysis({
        "game_pk": 1, "game_date": yesterday, "away_team": "A", "home_team": "B",
        "away_model_prob": 0.6, "home_model_prob": 0.4,
    })
    _mock_schedule(monkeypatch, [_eligible_game(game_pk=2)])

    # La única fila guardada es de AYER -- hoy sigue con 0 de 1 elegibles.
    assert check_script.already_ran_today() is False


def test_already_ran_today_is_false_when_run_crashed_partway(tmp_path, monkeypatch):
    # El escenario real que motivó este criterio: run_pipeline() guarda
    # cada fila en un loop SIN try/except por-ítem (a diferencia de
    # analyze_today(), que sí aísla errores por juego) -- un crash a
    # medias deja algunas filas guardadas y el resto del día sin tocar.
    # "existe al menos una fila" NO debe leerse como "ya corrió completo".
    db_mod = _isolated_db(tmp_path, monkeypatch)
    db_mod.init_db()
    today = date.today().strftime("%Y-%m-%d")
    db_mod.save_analysis({
        "game_pk": 1, "game_date": today, "away_team": "A", "home_team": "B",
        "away_model_prob": 0.6, "home_model_prob": 0.4,
    })
    # 3 juegos elegibles hoy, pero solo 1 se alcanzó a guardar.
    _mock_schedule(monkeypatch, [_eligible_game(1), _eligible_game(2), _eligible_game(3)])

    assert check_script.already_ran_today() is False


def test_already_ran_today_is_true_on_a_day_with_no_eligible_games(tmp_path, monkeypatch):
    # Día libre / todo pospuesto o TBD: 0 elegibles, 0 filas -- no hay
    # nada que analizar, no tiene sentido que el respaldo reintente cada
    # 2 horas gastando presupuesto de Odds API por un día vacío.
    _isolated_db(tmp_path, monkeypatch)
    _mock_schedule(monkeypatch, [
        _eligible_game(abstract_state="Postponed"),
        _eligible_game(away_pitcher_id=None),
    ])

    assert check_script.already_ran_today() is True


def test_count_eligible_games_today_excludes_non_preview_and_tbd_games(monkeypatch):
    # Mismo criterio de descarte que main.py::analyze_today() aplica
    # ANTES de tocar ninguna stats API -- no debe contar como "elegible"
    # un juego pospuesto/en curso/terminado, ni uno con abridor TBD.
    _mock_schedule(monkeypatch, [
        _eligible_game(game_pk=1),                                    # elegible
        _eligible_game(game_pk=2, abstract_state="Live"),              # en curso -- no cuenta
        _eligible_game(game_pk=3, abstract_state="Final"),             # terminado -- no cuenta
        _eligible_game(game_pk=4, away_pitcher_id=None),               # TBD visitante -- no cuenta
        _eligible_game(game_pk=5, home_pitcher_id=None),               # TBD local -- no cuenta
        _eligible_game(game_pk=6),                                    # elegible
    ])

    assert check_script._count_eligible_games_today() == 2
