"""
Pruebas de main._persist_game_results() -- PASO 4 de la auditoría externa:
antes, un fallo de save_picks() DESPUÉS de save_analysis() ya comiteado
dejaba el juego con análisis pero sin picks, Y no estaba atrapado por
juego, así que abortaba el resto del loop (los juegos siguientes de esa
corrida nunca se analizaban). Estas pruebas no tocan la base real --
mockean save_game_results() directamente para aislar el control de flujo
del loop, sin depender de la atomicidad interna (esa ya se prueba contra
la DB real en tests/test_database.py::test_save_game_results_rolls_back_all_three_if_picks_fail).
"""

import main


def _fake_result(game_pk, picks=None):
    return {
        "game_pk": game_pk, "game_date": "2026-07-05",
        "away_team": f"Away{game_pk}", "home_team": f"Home{game_pk}",
        "_feature_snapshot": {"away_era": 3.5},
        "_picks": picks if picks is not None else [
            {"market": "moneyline", "selection": "home", "model_prob": 0.6, "market_prob": 0.5,
             "edge": 0.1, "ev": 0.05, "odds_used": -110, "forced": False},
        ],
    }


def test_continues_processing_remaining_games_after_one_fails(monkeypatch):
    calls = []

    def fake_save_game_results(row, snapshot, picks, model_version):
        calls.append(row["game_pk"])
        if row["game_pk"] == 1:
            raise RuntimeError("fallo simulado guardando picks del juego 1")

    monkeypatch.setattr(main, "save_game_results", fake_save_game_results)

    results = [_fake_result(1), _fake_result(2), _fake_result(3)]

    picks_by_game, all_picks_rows, n_errors = main._persist_game_results(results, in_calibration_phase=False)

    # Los 3 juegos se intentaron -- el fallo del primero NO abortó el loop.
    assert calls == [1, 2, 3]
    assert n_errors == 1

    # El juego que falló no aparece en los resultados persistidos...
    assert 1 not in picks_by_game
    # ...pero los que sí se guardaron bien, sí.
    assert 2 in picks_by_game
    assert 3 in picks_by_game
    assert {row["game_pk"] for row in all_picks_rows} == {2, 3}


def test_all_games_succeed_when_no_failures(monkeypatch):
    saved = []
    monkeypatch.setattr(main, "save_game_results",
                         lambda row, snapshot, picks, model_version: saved.append(row["game_pk"]))

    results = [_fake_result(1), _fake_result(2)]
    picks_by_game, all_picks_rows, n_errors = main._persist_game_results(results, in_calibration_phase=False)

    assert saved == [1, 2]
    assert n_errors == 0
    assert set(picks_by_game) == {1, 2}
    assert len(all_picks_rows) == 2


def test_calibration_phase_flag_is_stamped_on_every_pick(monkeypatch):
    monkeypatch.setattr(main, "save_game_results", lambda row, snapshot, picks, model_version: None)

    results = [_fake_result(1)]
    picks_by_game, _, _ = main._persist_game_results(results, in_calibration_phase=True)

    assert picks_by_game[1][0]["calibration_phase"] is True


def test_game_without_picks_is_not_added_to_picks_by_game(monkeypatch):
    monkeypatch.setattr(main, "save_game_results", lambda row, snapshot, picks, model_version: None)

    results = [_fake_result(1, picks=[])]
    picks_by_game, all_picks_rows, n_errors = main._persist_game_results(results, in_calibration_phase=False)

    assert n_errors == 0
    assert 1 not in picks_by_game
    assert all_picks_rows == []
