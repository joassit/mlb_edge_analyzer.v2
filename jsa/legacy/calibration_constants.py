"""Constantes de calibracion heredadas de `mlb_edge_analyzer.v2/config.py`,
copiadas TAL CUAL (nunca recalibradas aqui) -- ver `jsa/legacy/README.md`
para la procedencia completa de cada una.

Regla dura: recalibrar estos valores con datos propios de JSA es trabajo
de `jsa/historical/validation.py`, una vez que exista historial real
ingerido dentro de JSA. Mientras eso no pase, estos son los ÚNICOS
valores legítimos para los modelos legado -- no se inventan nuevos aquí."""

from __future__ import annotations

from jsa.legacy.skellam_model import skellam_win_prob

# Peso del abridor vs. bullpen en el score de pitcheo del heuristico
# (mlb_edge_analyzer.v2/config.py:23).
STARTER_WEIGHT = 0.65

# Ventaja de jugar en casa, sumada a la probabilidad cruda del local antes
# de normalizar (mlb_edge_analyzer.v2/config.py:28) -- basado en que,
# historicamente, los equipos locales en MLB ganan ~54% de los juegos.
HOME_FIELD_ADVANTAGE = 0.02

# Parametro k de dispersion del Binomial Negativo (varianza = mu + mu^2/k).
# Recalibrado 2026-07-12 con historical_engine contra las 4 temporadas
# consolidadas (2022-2025, ~9,700 juegos con resultado real): k=3.0 mejoro
# el Brier score de NegBin sobre el prior original de 7.0 en las 4
# temporadas individualmente (mlb_edge_analyzer.v2/config.py:55-68).
NEGBIN_DISPERSION = 3.0

# Contraccion hacia 0.5 de la probabilidad cruda de Skellam:
# p_calibrada = 0.5 + alpha * (p_cruda - 0.5). El barrido de calibracion
# sobre las 4 temporadas historicas (2022-2025, 8,852 juegos con resultado
# real, 2026-07-12) confirmo que Skellam esta estructuralmente
# SOBRECONFIADO -- alpha=0.5 mejoro el Brier en las 4 temporadas SIN
# EXCEPCION (mlb_edge_analyzer.v2/config.py:70-91). El heuristico NO se
# contrae (alpha=1.0 ya optimo de fabrica) -- por eso HEURISTIC_SHRINKAGE_ALPHA
# no existe como constante separada, ver heuristic_model.py.
SKELLAM_SHRINKAGE_ALPHA = 0.5

# OPS/ERA de liga de referencia si no hay contexto de liga real disponible
# (mismo fallback que usaba el proyecto viejo).
LEAGUE_AVG_ERA_FALLBACK = 4.30
LEAGUE_AVG_OPS_FALLBACK = 0.750


def calibrated_skellam_win_prob(mu_team: float, mu_opponent: float, alpha: float = SKELLAM_SHRINKAGE_ALPHA) -> float:
    """Probabilidad de Skellam YA con la contraccion de calibracion
    legada aplicada -- esta es la version que se compara como baseline en
    `historical/validation.py`, no la cruda de `skellam_model.py`."""
    raw = skellam_win_prob(mu_team, mu_opponent)
    return 0.5 + alpha * (raw - 0.5)
