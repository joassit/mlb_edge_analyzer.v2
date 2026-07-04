"""
Pruebas de la lógica de Brier Score / accuracy, usando objetos simples
que imitan la forma de GameAnalysis/ActualResult sin tocar la base de datos.
"""


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
