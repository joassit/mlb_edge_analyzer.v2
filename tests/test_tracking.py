"""
Pruebas de la lógica de Brier Score / accuracy, usando objetos simples
que imitan la forma de GameAnalysis/ActualResult sin tocar la base de datos.
"""

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.database as database
import tracking.results_tracker as results_tracker


class _FakePrediction:
    def __init__(self, home_model_prob):
        self.home_model_prob = home_model_prob


class _FakeResult:
    def __init__(self, winner):
        self.winner = winner


def _brier_and_accuracy(rows):
    """Replica la lógica de compute_metrics() sin tocar SQLAlchemy/DB."""
    correct = 0
    brier_sum = 0.0
    for pred, result in rows:
        actual_home_win = 1 if result.winner == "home" else 0
        predicted_home = pred.home_model_prob > 0.5
        actual_home = actual_home_win == 1
        if predicted_home == actual_home:
            correct += 1
        brier_sum += (pred.home_model_prob - actual_home_win) ** 2
    n = len(rows)
    return correct / n, brier_sum / n


def test_perfect_predictions_give_zero_brier_score():
    rows = [
        (_FakePrediction(1.0), _FakeResult("home")),
        (_FakePrediction(0.0), _FakeResult("away")),
    ]
    accuracy, brier = _brier_and_accuracy(rows)
    assert accuracy == 1.0
    assert brier == 0.0


def test_always_50_50_gives_quarter_brier_score():
    rows = [
        (_FakePrediction(0.5), _FakeResult("home")),
        (_FakePrediction(0.5), _FakeResult("away")),
    ]
    _, brier = _brier_and_accuracy(rows)
    assert abs(brier - 0.25) < 1e-9


def test_worst_case_predictions_give_high_brier_score():
    rows = [
        (_FakePrediction(1.0), _FakeResult("away")),  # dijo seguro local, ganó visitante
        (_FakePrediction(0.0), _FakeResult("home")),  # dijo seguro visitante, ganó local
    ]
    accuracy, brier = _brier_and_accuracy(rows)
    assert accuracy == 0.0
    assert brier == 1.0


def test_compute_clv_performance_averages_across_settled_bets(tmp_path, monkeypatch):
    temp_engine = create_engine(f"sqlite:///{tmp_path}/clv_test.db")
    database.Base.metadata.create_all(temp_engine)
    TempSession = sessionmaker(bind=temp_engine)
    monkeypatch.setattr(results_tracker, "SessionLocal", TempSession)

    today = date.today().isoformat()
    session = TempSession()
    session.add(database.Bet(game_pk=1, game_date=today, side="away", odds=-135, model_prob=0.6, stake=1.0, clv=0.05))
    session.add(database.Bet(game_pk=2, game_date=today, side="home", odds=120, model_prob=0.5, stake=1.0, clv=-0.02))
    session.commit()
    session.close()

    perf = results_tracker.compute_clv_performance(days=30)

    assert perf["n_bets"] == 2
    assert abs(perf["avg_clv"] - 0.015) < 1e-9
    assert perf["positive_clv_rate"] == 0.5


def test_compute_clv_performance_empty_when_no_bets_have_closing_line(tmp_path, monkeypatch):
    temp_engine = create_engine(f"sqlite:///{tmp_path}/clv_empty_test.db")
    database.Base.metadata.create_all(temp_engine)
    TempSession = sessionmaker(bind=temp_engine)
    monkeypatch.setattr(results_tracker, "SessionLocal", TempSession)

    perf = results_tracker.compute_clv_performance(days=30)

    assert perf["n_bets"] == 0
    assert perf["avg_clv"] is None


def test_compute_metrics_includes_market_brier_benchmark(tmp_path, monkeypatch):
    temp_engine = create_engine(f"sqlite:///{tmp_path}/metrics_test.db")
    database.Base.metadata.create_all(temp_engine)
    TempSession = sessionmaker(bind=temp_engine)
    monkeypatch.setattr(results_tracker, "SessionLocal", TempSession)

    today = date.today().isoformat()
    session = TempSession()
    # El modelo acertó con alta confianza (0.9); el mercado estaba casi en
    # pick'em (0.5) -> el modelo debe ganarle en Brier Score al mercado.
    session.add(database.GameAnalysis(
        game_pk=1, game_date=today, away_team="A", home_team="B",
        home_model_prob=0.9, home_market_no_vig_prob=0.5,
    ))
    session.add(database.ActualResult(
        game_pk=1, game_date=today, home_score=5, away_score=2, winner="home", total_runs=7,
    ))
    session.commit()
    session.close()

    metrics = results_tracker.compute_metrics(days=30)

    assert metrics["market_n_games"] == 1
    assert metrics["market_brier_score"] is not None
    assert metrics["brier_score"] < metrics["market_brier_score"]


def test_compute_metrics_market_brier_is_none_without_market_data(tmp_path, monkeypatch):
    temp_engine = create_engine(f"sqlite:///{tmp_path}/metrics_no_market_test.db")
    database.Base.metadata.create_all(temp_engine)
    TempSession = sessionmaker(bind=temp_engine)
    monkeypatch.setattr(results_tracker, "SessionLocal", TempSession)

    today = date.today().isoformat()
    session = TempSession()
    session.add(database.GameAnalysis(
        game_pk=1, game_date=today, away_team="A", home_team="B", home_model_prob=0.7,
    ))
    session.add(database.ActualResult(
        game_pk=1, game_date=today, home_score=5, away_score=2, winner="home", total_runs=7,
    ))
    session.commit()
    session.close()

    metrics = results_tracker.compute_metrics(days=30)

    assert metrics["market_n_games"] == 0
    assert metrics["market_brier_score"] is None


# --- count_liquidated_picks_with_market_odds(): fase de calibración del
# EDGE apostable (config.MIN_LIQUIDATED_PICKS_FOR_CALIBRATION) -- distinto
# de compute_metrics()/compute_calibration(), que miden si la PROBABILIDAD
# cruda del modelo está bien calibrada, sin importar si hubo cuota real.

def _add_pick(session, game_pk, odds_used=None, result="pending",
              market="moneyline", selection="away", game_date="2026-01-01"):
    session.add(database.Pick(
        game_pk=game_pk, game_date=game_date, market=market, selection=selection,
        model_prob=0.6, odds_used=odds_used, result=result,
    ))


def test_count_liquidated_picks_is_zero_with_empty_db(tmp_path, monkeypatch):
    temp_engine = create_engine(f"sqlite:///{tmp_path}/count_empty_test.db")
    database.Base.metadata.create_all(temp_engine)
    monkeypatch.setattr(results_tracker, "SessionLocal", sessionmaker(bind=temp_engine))

    assert results_tracker.count_liquidated_picks_with_market_odds() == 0


def test_count_liquidated_picks_ignores_game_with_final_result_but_no_market_odds(tmp_path, monkeypatch):
    # El caso real de los 13 juegos del 2026-07-05: resultado final
    # registrado, pero CERO cuotas de mercado cargadas para ningún
    # mercado -- nunca hubo un edge real que poner a prueba, así que no
    # debe contar hacia esta calibración (aunque sí cuenta para
    # print_calibration_report()/compute_calibration(), que miden algo
    # distinto: la probabilidad cruda, no el edge).
    temp_engine = create_engine(f"sqlite:///{tmp_path}/count_no_odds_test.db")
    database.Base.metadata.create_all(temp_engine)
    TempSession = sessionmaker(bind=temp_engine)
    monkeypatch.setattr(results_tracker, "SessionLocal", TempSession)

    session = TempSession()
    session.add(database.GameAnalysis(
        game_pk=1, game_date="2026-07-05", away_team="A", home_team="B",
        away_model_prob=0.4, home_model_prob=0.6,
    ))
    session.add(database.ActualResult(
        game_pk=1, game_date="2026-07-05", home_score=5, away_score=2, winner="home", total_runs=7,
    ))
    _add_pick(session, 1, odds_used=None, result="pending")  # "sin cuotas cargadas para este mercado"
    session.commit()
    session.close()

    assert results_tracker.count_liquidated_picks_with_market_odds() == 0


def test_count_liquidated_picks_excludes_pending_picks_even_with_real_odds(tmp_path, monkeypatch):
    temp_engine = create_engine(f"sqlite:///{tmp_path}/count_pending_test.db")
    database.Base.metadata.create_all(temp_engine)
    TempSession = sessionmaker(bind=temp_engine)
    monkeypatch.setattr(results_tracker, "SessionLocal", TempSession)

    session = TempSession()
    _add_pick(session, 1, odds_used=-150, result="pending")  # cuota real, pero el juego no ha terminado
    session.commit()
    session.close()

    assert results_tracker.count_liquidated_picks_with_market_odds() == 0


def test_count_liquidated_picks_counts_settled_pick_with_real_odds(tmp_path, monkeypatch):
    temp_engine = create_engine(f"sqlite:///{tmp_path}/count_settled_test.db")
    database.Base.metadata.create_all(temp_engine)
    TempSession = sessionmaker(bind=temp_engine)
    monkeypatch.setattr(results_tracker, "SessionLocal", TempSession)

    session = TempSession()
    _add_pick(session, 1, odds_used=-150, result="win")
    session.commit()
    session.close()

    assert results_tracker.count_liquidated_picks_with_market_odds() == 1


def test_count_liquidated_picks_counts_each_settled_pick_separately(tmp_path, monkeypatch):
    # Un mismo juego puede tener hasta 3 picks (ML/RL/Totales) -- cada uno
    # es un edge distinto puesto a prueba contra su propia cuota, así que
    # cuentan por separado (a diferencia del criterio viejo, que
    # deduplicaba por game_pk porque medía JUEGOS, no edges).
    temp_engine = create_engine(f"sqlite:///{tmp_path}/count_multi_test.db")
    database.Base.metadata.create_all(temp_engine)
    TempSession = sessionmaker(bind=temp_engine)
    monkeypatch.setattr(results_tracker, "SessionLocal", TempSession)

    session = TempSession()
    _add_pick(session, 1, odds_used=-150, result="win", market="moneyline", selection="away")
    _add_pick(session, 1, odds_used=120, result="loss", market="run_line", selection="home")
    _add_pick(session, 1, odds_used=-110, result="push", market="totals", selection="over")
    session.commit()
    session.close()

    assert results_tracker.count_liquidated_picks_with_market_odds() == 3


def test_count_liquidated_picks_has_no_days_window_parameter():
    # A diferencia de compute_metrics(days=N), esto no tiene ventana de
    # días -- importa el histórico TOTAL acumulado, no uno reciente.
    import inspect
    sig = inspect.signature(results_tracker.count_liquidated_picks_with_market_odds)
    assert "days" not in sig.parameters


def test_compute_pick_performance_separates_real_from_forced_picks(tmp_path, monkeypatch):
    temp_engine = create_engine(f"sqlite:///{tmp_path}/pick_perf_test.db")
    database.Base.metadata.create_all(temp_engine)
    TempSession = sessionmaker(bind=temp_engine)
    monkeypatch.setattr(results_tracker, "SessionLocal", TempSession)

    today = date.today().isoformat()
    session = TempSession()
    # Pick real, ganador
    session.add(database.Pick(
        game_pk=1, game_date=today, market="moneyline", selection="away",
        model_prob=0.65, forced=False, result="win", profit_unit=0.8,
    ))
    # Pick forzado, perdedor -- no debe contaminar el desempeño "real"
    session.add(database.Pick(
        game_pk=2, game_date=today, market="totals", selection="over",
        model_prob=0.50, forced=True, result="loss", profit_unit=-1.0,
    ))
    session.commit()
    session.close()

    perf = results_tracker.compute_pick_performance(days=30)

    assert perf["overall_real"]["n_picks"] == 1
    assert perf["overall_real"]["win_rate"] == 1.0
    assert perf["overall_forced"]["n_picks"] == 1
    assert perf["overall_forced"]["win_rate"] == 0.0
    assert perf["by_market"]["moneyline"]["real"]["n_picks"] == 1
    assert perf["by_market"]["totals"]["forced"]["n_picks"] == 1
    assert perf["by_market"]["run_line"]["real"]["n_picks"] == 0


def test_compute_pick_performance_treats_push_as_no_stake_loss_neutral(tmp_path, monkeypatch):
    temp_engine = create_engine(f"sqlite:///{tmp_path}/pick_push_test.db")
    database.Base.metadata.create_all(temp_engine)
    TempSession = sessionmaker(bind=temp_engine)
    monkeypatch.setattr(results_tracker, "SessionLocal", TempSession)

    today = date.today().isoformat()
    session = TempSession()
    session.add(database.Pick(
        game_pk=3, game_date=today, market="totals", selection="over",
        model_prob=0.55, forced=False, result="push", profit_unit=0.0,
    ))
    session.commit()
    session.close()

    perf = results_tracker.compute_pick_performance(days=30)

    assert perf["overall_real"]["n_picks"] == 1
    assert perf["overall_real"]["win_rate"] is None  # sin decididos (push no cuenta como ganado/perdido)
    assert perf["overall_real"]["roi"] == 0.0


class _FakeValidationPrediction:
    def __init__(self, game_pk=1, away_model_prob=0.4, home_model_prob=0.6,
                 away_skellam_prob=0.45, home_skellam_prob=0.55):
        self.game_pk = game_pk
        self.away_model_prob = away_model_prob
        self.home_model_prob = home_model_prob
        self.away_skellam_prob = away_skellam_prob
        self.home_skellam_prob = home_skellam_prob


def test_validate_probabilities_accepts_valid_row():
    assert results_tracker.validate_probabilities(_FakeValidationPrediction()) is True


def test_validate_probabilities_rejects_probabilities_that_dont_sum_to_one():
    bad = _FakeValidationPrediction(away_model_prob=0.6, home_model_prob=0.6)
    assert results_tracker.validate_probabilities(bad) is False


def test_validate_probabilities_rejects_out_of_range_values():
    bad = _FakeValidationPrediction(away_model_prob=1.5, home_model_prob=-0.5)
    assert results_tracker.validate_probabilities(bad) is False


def test_validate_probabilities_ignores_missing_skellam_fields():
    # Snapshots viejos pueden no tener Skellam -- no debe fallar por eso.
    row = _FakeValidationPrediction(away_skellam_prob=None, home_skellam_prob=None)
    assert results_tracker.validate_probabilities(row) is True


def test_compute_metrics_excludes_rows_that_fail_probability_validation(tmp_path, monkeypatch):
    temp_engine = create_engine(f"sqlite:///{tmp_path}/metrics_invalid_test.db")
    database.Base.metadata.create_all(temp_engine)
    TempSession = sessionmaker(bind=temp_engine)
    monkeypatch.setattr(results_tracker, "SessionLocal", TempSession)

    today = date.today().isoformat()
    session = TempSession()
    # Fila válida
    session.add(database.GameAnalysis(
        game_pk=1, game_date=today, away_team="A", home_team="B",
        away_model_prob=0.4, home_model_prob=0.6,
    ))
    session.add(database.ActualResult(
        game_pk=1, game_date=today, home_score=5, away_score=2, winner="home", total_runs=7,
    ))
    # Fila corrupta: las probabilidades no suman 1 -- no debe contaminar Brier/accuracy
    session.add(database.GameAnalysis(
        game_pk=2, game_date=today, away_team="C", home_team="D",
        away_model_prob=0.6, home_model_prob=0.6,
    ))
    session.add(database.ActualResult(
        game_pk=2, game_date=today, home_score=3, away_score=1, winner="home", total_runs=4,
    ))
    session.commit()
    session.close()

    metrics = results_tracker.compute_metrics(days=30)

    assert metrics["n_games"] == 1


def test_compute_pick_performance_empty_when_no_picks_settled(tmp_path, monkeypatch):
    temp_engine = create_engine(f"sqlite:///{tmp_path}/pick_empty_test.db")
    database.Base.metadata.create_all(temp_engine)
    TempSession = sessionmaker(bind=temp_engine)
    monkeypatch.setattr(results_tracker, "SessionLocal", TempSession)

    perf = results_tracker.compute_pick_performance(days=30)

    assert perf["overall_real"]["n_picks"] == 0
    assert perf["overall_forced"]["n_picks"] == 0
