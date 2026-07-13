"""Probabilidad de victoria via distribucion de Skellam -- portado tal cual
de `mlb_edge_analyzer.v2/model/skellam_model.py`. Ver `jsa/legacy/README.md`:
rama secundaria de benchmarking, nunca el motor primario de JSA.

Nota: `jsa/engine/projected_runs.py::skellam_win_prob` tiene la MISMA
formula matematica (deliberado, no una casualidad: es tecnica estandar de
diferencia de dos Poisson + renormalizacion por empate), pero SIN la
contraccion de calibracion `SKELLAM_SHRINKAGE_ALPHA` -- esa funcion
alimenta el `raw_probability` honesto de JSA (sin pretender estar
calibrado). Este modulo existe para poder aplicar la contraccion legada
como baseline de comparacion en `historical/validation.py`, ver
`calibration_constants.py::calibrated_skellam_win_prob`."""

from __future__ import annotations

from scipy.stats import skellam


def skellam_win_prob(mu_team: float, mu_opponent: float) -> float:
    """Probabilidad de que un equipo gane, via Skellam + renormalizacion
    por empate (un juego de MLB real no puede terminar empatado)."""
    mu_team = max(mu_team, 0.05)
    mu_opponent = max(mu_opponent, 0.05)
    prob_win = 1.0 - float(skellam.cdf(0, mu_team, mu_opponent))
    prob_loss = float(skellam.cdf(-1, mu_team, mu_opponent))
    denom = prob_win + prob_loss
    return (prob_win / denom) if denom > 0 else 0.5
