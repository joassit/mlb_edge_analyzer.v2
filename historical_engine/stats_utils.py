"""
Funciones estadísticas puras (sin DB, sin estado) que alimentan tanto
validation.py como runs_analysis.py -- separadas en su propio módulo para
no duplicar la misma fórmula en dos lugares que podrían desincronizarse.

Todas reciben listas/arrays de Python planos, nunca objetos ORM -- se
pueden probar de forma completamente determinista sin ninguna base de datos.
"""

import math
import random


def brier_score(probs: list[float], outcomes: list[int]) -> float | None:
    """Brier score clásico: mean((p - outcome)^2). None si la lista está vacía."""
    if not probs:
        return None
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)


def log_loss(probs: list[float], outcomes: list[int], eps: float = 1e-15) -> float | None:
    if not probs:
        return None
    total = 0.0
    for p, o in zip(probs, outcomes):
        p_clipped = min(max(p, eps), 1 - eps)
        total += -(o * math.log(p_clipped) + (1 - o) * math.log(1 - p_clipped))
    return total / len(probs)


def mae(pred: list[float], actual: list[float]) -> float | None:
    if not pred:
        return None
    return sum(abs(p - a) for p, a in zip(pred, actual)) / len(pred)


def rmse(pred: list[float], actual: list[float]) -> float | None:
    if not pred:
        return None
    return math.sqrt(sum((p - a) ** 2 for p, a in zip(pred, actual)) / len(pred))


def bias(pred: list[float], actual: list[float]) -> float | None:
    """Sesgo direccional: promedio de (pred - actual). Positivo = el modelo
    sobreproyecta; negativo = subproyecta."""
    if not pred:
        return None
    return sum(p - a for p, a in zip(pred, actual)) / len(pred)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def pearson_corr(x: list[float], y: list[float]) -> float | None:
    if len(x) < 2:
        return None
    mx, my = _mean(x), _mean(y)
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    den_x = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    den_y = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def _rank(xs: list[float]) -> list[float]:
    """Rangos con empates promediados (mismo criterio que scipy.stats.rankdata
    con method='average')."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def spearman_corr(x: list[float], y: list[float]) -> float | None:
    if len(x) < 2:
        return None
    return pearson_corr(_rank(x), _rank(y))


def r_squared(pred: list[float], actual: list[float]) -> float | None:
    """Coeficiente de determinación de pred sobre actual (1 - SS_res/SS_tot).
    Puede ser negativo si el modelo predice peor que la media -- se
    reporta tal cual, sin recortar a 0 (recortar ocultaría un modelo
    realmente malo)."""
    if len(actual) < 2:
        return None
    mean_actual = _mean(actual)
    ss_tot = sum((a - mean_actual) ** 2 for a in actual)
    if ss_tot == 0:
        return None
    ss_res = sum((a - p) ** 2 for p, a in zip(pred, actual))
    return 1 - (ss_res / ss_tot)


def bootstrap_ci(values: list[float], statistic_fn, n_resamples: int = 1000,
                  ci: float = 0.95, seed: int = 42) -> tuple[float, float] | None:
    """Intervalo de confianza por bootstrap no paramétrico -- remuestrea
    `values` con reemplazo `n_resamples` veces, aplica `statistic_fn` a
    cada remuestra, y devuelve el percentil (low, high) correspondiente a
    `ci`. `seed` fijo para que el resultado sea reproducible entre corridas
    del mismo dato (requisito de auditoría: dos corridas del mismo
    histórico deben dar el mismo intervalo)."""
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    n = len(values)
    stats = []
    for _ in range(n_resamples):
        resample = [values[rng.randrange(n)] for _ in range(n)]
        stats.append(statistic_fn(resample))
    stats.sort()
    alpha = 1 - ci
    low_idx = int((alpha / 2) * n_resamples)
    high_idx = int((1 - alpha / 2) * n_resamples) - 1
    high_idx = min(high_idx, n_resamples - 1)
    return stats[low_idx], stats[high_idx]


_CALIBRATION_BUCKETS = [
    (0.50, 0.55, "50-55%"), (0.55, 0.60, "55-60%"), (0.60, 0.65, "60-65%"),
    (0.65, 0.70, "65-70%"), (0.70, 0.75, "70-75%"), (0.75, 1.01, "75%+"),
]


def calibration_buckets(confidences: list[float], hits: list[bool]) -> list[dict]:
    """Agrupa (confianza declarada, acierto) en los 6 buckets estándar del
    proyecto y devuelve n/hits/avg_confidence/hit_rate/gap por bucket."""
    totals = [{"n": 0, "hits": 0, "confidence_sum": 0.0} for _ in _CALIBRATION_BUCKETS]
    for conf, hit in zip(confidences, hits):
        for idx, (low, high, _label) in enumerate(_CALIBRATION_BUCKETS):
            if low <= conf < high:
                totals[idx]["n"] += 1
                totals[idx]["hits"] += 1 if hit else 0
                totals[idx]["confidence_sum"] += conf
                break

    result = []
    for (low, high, label), t in zip(_CALIBRATION_BUCKETS, totals):
        if t["n"] == 0:
            result.append({"label": label, "low": low, "high": high, "n": 0,
                            "hits": 0, "avg_confidence": None, "hit_rate": None, "gap": None})
            continue
        hit_rate = t["hits"] / t["n"]
        avg_confidence = t["confidence_sum"] / t["n"]
        result.append({"label": label, "low": low, "high": high, "n": t["n"], "hits": t["hits"],
                        "avg_confidence": avg_confidence, "hit_rate": hit_rate, "gap": hit_rate - avg_confidence})
    return result


def expected_calibration_error(buckets: list[dict], total_n: int) -> float | None:
    """ECE: promedio de |gap| ponderado por n de cada bucket."""
    populated = [b for b in buckets if b["n"] > 0]
    if not populated or total_n == 0:
        return None
    return sum(abs(b["gap"]) * b["n"] for b in populated) / total_n


def maximum_calibration_error(buckets: list[dict]) -> float | None:
    """MCE: el |gap| más grande entre los buckets con datos."""
    populated = [b for b in buckets if b["n"] > 0 and b["gap"] is not None]
    if not populated:
        return None
    return max(abs(b["gap"]) for b in populated)


def sharpness(confidences: list[float]) -> float | None:
    """Qué tan lejos de 50/50 (indeciso) están, en promedio, las
    confianzas declaradas -- un modelo 'sharp' se compromete con
    predicciones alejadas de 0.5 (no necesariamente bien calibradas,
    ver ECE para eso)."""
    if not confidences:
        return None
    return _mean([abs(c - 0.5) for c in confidences])
