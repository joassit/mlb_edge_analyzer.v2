"""Seccion 12.8 del spec JSA v3.0 -- pruebas formales de significancia,
compartidas por todos los candidate audits (Statcast, Trend, Historical,
Game Flow, Elo/Pythagorean, y ahora Rule candidate audit). Unico lugar
donde vive esta logica -- antes `paired_bootstrap_ci` (como
`_paired_bootstrap_ci`) estaba duplicada: definida en
`discriminative_audit.py` e importada de ahi por otros 4 modulos.

Las 3 pruebas operan sobre el MISMO par de listas `(baseline_pairs,
alt_pairs)`: predicciones LOSO ya calculadas (`calibration.py::
loso_fit_and_score()["loso_pairs"]`), alineadas juego a juego (mismo
juego en la posicion i de ambas listas) -- nunca vuelven a entrenar nada,
solo comparan 2 vectores de prediccion ya obtenidos.

Alpha consistente en las 3 pruebas: 0.10 (dos colas) -- el mismo nivel
que ya usaba el CI de bootstrap al 90% (percentiles 5/95) desde el
primer candidate audit de este proyecto (Statcast). `full_significance_
report()` exige que las 3 pruebas Y el tamano de efecto minimo esten de
acuerdo antes de considerar una alternativa como mejora real -- el
criterio mas estricto usado en el proyecto hasta ahora, reservado para
decidir si una regla/pilar pasa de `experimental` a `active` (Fase 3,
Seccion 6.6)."""

from __future__ import annotations

import numpy as np
from scipy.stats import chi2

_ALPHA = 0.10
_MIN_EFFECT_SIZE_BRIER = 0.001


def paired_bootstrap_ci(
    baseline_pairs: list[tuple[float, int]], alt_pairs: list[tuple[float, int]], n_resamples: int = 500, seed: int = 0
) -> dict | None:
    """CI 90% del delta de Brier (alt - baseline) via bootstrap PAREADO
    sobre predicciones LOSO ya calculadas (nunca vuelve a entrenar nada,
    solo remuestrea que juegos entran en el promedio) -- para no aceptar
    un delta como real si el intervalo cruza cero."""
    n = len(baseline_pairs)
    if n == 0 or len(alt_pairs) != n:
        return None
    rng = np.random.default_rng(seed)
    base_sq_err = np.array([(p - y) ** 2 for p, y in baseline_pairs])
    alt_sq_err = np.array([(p - y) ** 2 for p, y in alt_pairs])
    diffs = alt_sq_err - base_sq_err
    resampled = np.array([diffs[rng.integers(0, n, n)].mean() for _ in range(n_resamples)])
    return {
        "delta_brier_mean": float(diffs.mean()),
        "ci_5": float(np.percentile(resampled, 5)),
        "ci_95": float(np.percentile(resampled, 95)),
        "significant": bool(np.percentile(resampled, 5) > 0 or np.percentile(resampled, 95) < 0),
    }


def _is_correct(pair: tuple[float, int]) -> bool:
    p, y = pair
    return (p >= 0.5) == bool(y)


def mcnemar_test(baseline_pairs: list[tuple[float, int]], alt_pairs: list[tuple[float, int]]) -> dict | None:
    """McNemar's test pareado sobre aciertos/errores binarios (`p>=0.5 ==
    y`, mismo criterio que `validation.py::accuracy()`). Solo los casos
    DISCORDANTES aportan evidencia de diferencia: `b` = baseline acierta
    Y alt falla; `c` = al reves. Estadistico con correccion de
    continuidad, chi2 con 1 grado de libertad. `b+c==0` (ningun caso
    discordante) => sin evidencia de diferencia, nunca un error."""
    n = len(baseline_pairs)
    if n == 0 or len(alt_pairs) != n:
        return None
    b = c = 0
    for bp, ap in zip(baseline_pairs, alt_pairs):
        base_correct = _is_correct(bp)
        alt_correct = _is_correct(ap)
        if base_correct and not alt_correct:
            b += 1
        elif not base_correct and alt_correct:
            c += 1
    if b + c == 0:
        return {"b": b, "c": c, "statistic": 0.0, "p_value": 1.0, "significant": False, "alt_better": False}
    statistic = (abs(b - c) - 1) ** 2 / (b + c)
    p_value = float(chi2.sf(statistic, df=1))
    return {
        "b": b, "c": c,
        "statistic": float(statistic),
        "p_value": p_value,
        "significant": bool(p_value < _ALPHA),
        "alt_better": bool(c > b),
    }


def permutation_test_delta_brier(
    baseline_pairs: list[tuple[float, int]], alt_pairs: list[tuple[float, int]], n_permutations: int = 500, seed: int = 0
) -> dict | None:
    """Test de permutacion PAREADO (sign-flip) sobre el delta de Brier
    (alt - baseline): bajo la hipotesis nula de que baseline/alt son
    intercambiables juego a juego, cada permutacion decide de nuevo con
    probabilidad 0.5 cual de los 2 se llama "baseline" y cual "alt" DENTRO
    de cada par -- nunca reordena entre juegos distintos, preserva la
    estructura pareada. p-valor de dos colas: proporcion de deltas
    permutados con `|delta| >= |delta observado|`. Complementa el
    bootstrap (que remuestrea CUALES juegos entran) con una prueba
    distinta (que etiqueta es baseline/alt en cada juego) -- ninguna
    reemplaza a la otra."""
    n = len(baseline_pairs)
    if n == 0 or len(alt_pairs) != n:
        return None
    rng = np.random.default_rng(seed)
    base_sq_err = np.array([(p - y) ** 2 for p, y in baseline_pairs])
    alt_sq_err = np.array([(p - y) ** 2 for p, y in alt_pairs])
    diffs = alt_sq_err - base_sq_err  # positivo = alt peor
    observed = float(diffs.mean())
    flips = rng.integers(0, 2, size=(n_permutations, n)) * 2 - 1  # +-1 por juego y por permutacion
    permuted_means = (diffs[None, :] * flips).mean(axis=1)
    p_value = float((np.abs(permuted_means) >= abs(observed)).mean())
    return {
        "observed_delta_brier_mean": observed,
        "p_value": p_value,
        "significant": bool(p_value < _ALPHA),
        "n_permutations": n_permutations,
    }


def full_significance_report(
    baseline_pairs: list[tuple[float, int]],
    alt_pairs: list[tuple[float, int]],
    min_effect_size: float = _MIN_EFFECT_SIZE_BRIER,
    n_resamples: int = 500,
    n_permutations: int = 500,
    seed: int = 0,
) -> dict:
    """Combina las 3 pruebas + el tamano de efecto minimo ya usado en todo
    el proyecto (Trend/Historical/Statcast/Elo-Pythagorean/Game Flow).
    `passes_all_three=True` unicamente si: el bootstrap Y McNemar Y la
    permutacion coinciden en que hay una diferencia real (cada uno con su
    propio p-valor/CI a alpha=0.10) Y la direccion es de MEJORA (delta de
    Brier negativo, McNemar con mas correcciones que reversiones) Y el
    tamano de efecto alcanza el minimo -- el criterio mas estricto usado
    en el proyecto hasta ahora, reservado para promover una regla/pilar de
    `experimental` a `active` (nunca para simplemente reportar un
    resultado, eso lo sigue haciendo cada bootstrap/McNemar/permutacion
    por separado)."""
    bootstrap = paired_bootstrap_ci(baseline_pairs, alt_pairs, n_resamples=n_resamples, seed=seed)
    mcnemar = mcnemar_test(baseline_pairs, alt_pairs)
    permutation = permutation_test_delta_brier(baseline_pairs, alt_pairs, n_permutations=n_permutations, seed=seed)
    if bootstrap is None or mcnemar is None or permutation is None:
        return {"bootstrap": bootstrap, "mcnemar": mcnemar, "permutation": permutation, "passes_all_three": False}

    delta = bootstrap["delta_brier_mean"]
    improves = delta < 0
    effect_size_ok = abs(delta) >= min_effect_size
    passes_all_three = bool(
        improves
        and effect_size_ok
        and bootstrap["significant"]
        and mcnemar["significant"]
        and mcnemar["alt_better"]
        and permutation["significant"]
    )
    return {
        "bootstrap": bootstrap,
        "mcnemar": mcnemar,
        "permutation": permutation,
        "improves": improves,
        "effect_size_ok": effect_size_ok,
        "passes_all_three": passes_all_three,
    }
