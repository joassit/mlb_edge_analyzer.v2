"""Contribucion real de cada pilar al Evidence Score, agregada sobre N
juegos -- la pieza que faltaba junto a `evidence_engine.compute_feature_
contribution()` (Seccion 7.2), que ya calcula esto por UN juego (weight x
advantage + Dominance Detector) y ya queda persistido en todo `JSAReport.
feature_contribution`, tanto en produccion (`storage/database.py::
jsa_reports.payload`) como en historico (`historical/db.py::
historical_report.payload`).

Deliberadamente puro y sin I/O -- igual que `engine/evidence_engine.py` --
para poder vivir en `jsa/analytics/` (paralelo a `engine/`, `domain/`,
`storage/`) en vez de junto al paquete historico: asi puede importarse
algun dia desde codigo de produccion sin violar la regla de aislamiento
que verifica `tests/test_production_isolation.py` (produccion nunca
importa el paquete historico ni el legado, pero SI puede importar de
`jsa/analytics`, que no depende de ninguno de los dos). El lado con I/O
que lee `historical_report` para alimentar esto vive en el paquete
historico, en `pillar_contribution.py` junto a `validation.py` y
`monte_carlo.py`.

Nunca recalcula el Evidence Score ni reevalua un pilar -- solo agrega la
contribucion YA calculada y persistida por juego. Vectorizado con numpy
(arreglos (n_games, 7) por metrica, una sola pasada) para escalar sin
esfuerzo a miles de juegos."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from jsa.domain.models import SEVEN_PILLARS, FeatureContributionEntry

# Contribucion porcentual por debajo de este umbral se cuenta como "casi
# nula" para `negligible_contribution_rate` -- convencion de reporte, no un
# parametro de modelo (no vive en jsa/config.py): no cambia ningun calculo
# del Evidence Score, solo como se resume su historial despues del hecho.
NEGLIGIBLE_CONTRIBUTION_THRESHOLD = 0.05


@dataclass
class PillarContributionStats:
    """Resumen agregado de un pilar sobre un conjunto de juegos."""

    pillar: str
    n_games: int
    mean_advantage: float
    mean_absolute_contribution: float
    mean_percentage_contribution: float
    median_percentage_contribution: float
    std_percentage_contribution: float
    p10_percentage_contribution: float
    p90_percentage_contribution: float
    dominance_warning_rate: float
    """Fraccion de juegos donde este pilar disparo `dominance_warning`
    (>GATE_DOMINANCE_THRESHOLD del Evidence Score, Seccion 7.2/10.2)."""
    top_contributor_rate: float
    """Fraccion de juegos donde este pilar tuvo la MAYOR contribucion
    porcentual de los 7 -- señal complementaria a `dominance_warning_rate`:
    identifica que pilar domina la decision en la practica aunque ningun
    juego individual cruce el umbral formal."""
    zero_advantage_rate: float
    """Fraccion de juegos donde `advantage==0` -- el pilar literalmente no
    tuvo nada que decir (dato faltante o matchup neutral)."""
    negligible_contribution_rate: float
    """Fraccion de juegos con contribucion porcentual por debajo de
    `NEGLIGIBLE_CONTRIBUTION_THRESHOLD`."""


@dataclass
class PillarContributionReport:
    n_games: int
    stats_by_pillar: dict[str, PillarContributionStats]
    most_dominant_pillar: str | None
    least_contributing_pillar: str | None


class PillarContributionAnalyzer:
    """Agrega `list[FeatureContributionEntry]` de N juegos. Sin estado
    entre llamadas -- `analyze()` es la unica operacion publica."""

    def analyze(self, games: list[list[FeatureContributionEntry]]) -> PillarContributionReport:
        if not games:
            return PillarContributionReport(n_games=0, stats_by_pillar={}, most_dominant_pillar=None, least_contributing_pillar=None)

        n_games = len(games)
        n_pillars = len(SEVEN_PILLARS)
        pillar_index = {p: i for i, p in enumerate(SEVEN_PILLARS)}

        advantage = np.zeros((n_games, n_pillars))
        abs_contrib = np.zeros((n_games, n_pillars))
        pct_contrib = np.zeros((n_games, n_pillars))
        dominance = np.zeros((n_games, n_pillars), dtype=bool)

        for row, entries in enumerate(games):
            by_pillar = {e.pillar: e for e in entries}
            for pillar, col in pillar_index.items():
                entry = by_pillar.get(pillar)
                if entry is None:
                    continue
                advantage[row, col] = entry.advantage
                abs_contrib[row, col] = entry.absolute_contribution
                pct_contrib[row, col] = entry.percentage_contribution
                dominance[row, col] = entry.dominance_warning

        # Pilar con mayor contribucion porcentual en cada juego (una fila =
        # un juego). ddof=0 para std (poblacional, no muestral: estamos
        # describiendo el conjunto completo analizado, no estimando a
        # partir de una muestra de un universo mayor).
        top_contributor_per_game = np.argmax(pct_contrib, axis=1)

        stats_by_pillar: dict[str, PillarContributionStats] = {}
        for pillar, col in pillar_index.items():
            stats_by_pillar[pillar] = PillarContributionStats(
                pillar=pillar,
                n_games=n_games,
                mean_advantage=float(np.mean(advantage[:, col])),
                mean_absolute_contribution=float(np.mean(abs_contrib[:, col])),
                mean_percentage_contribution=float(np.mean(pct_contrib[:, col])),
                median_percentage_contribution=float(np.median(pct_contrib[:, col])),
                std_percentage_contribution=float(np.std(pct_contrib[:, col])),
                p10_percentage_contribution=float(np.percentile(pct_contrib[:, col], 10)),
                p90_percentage_contribution=float(np.percentile(pct_contrib[:, col], 90)),
                dominance_warning_rate=float(np.mean(dominance[:, col])),
                top_contributor_rate=float(np.mean(top_contributor_per_game == col)),
                zero_advantage_rate=float(np.mean(advantage[:, col] == 0)),
                negligible_contribution_rate=float(np.mean(pct_contrib[:, col] < NEGLIGIBLE_CONTRIBUTION_THRESHOLD)),
            )

        # Si todos los pilares empatan (ej. un conjunto de juegos totalmente
        # neutral, sin señal en ningun pilar) max()/min() elegirian el mismo
        # pilar para ambos extremos por orden de iteracion -- eso reportaria
        # un pilar como "el mas dominante" y "el que menos aporta" a la vez,
        # que es contradictorio. Se deja None en vez de un resultado
        # arbitrario cuando no hay variacion real que distinguirlos.
        pct_means = {p: s.mean_percentage_contribution for p, s in stats_by_pillar.items()}
        if len(set(pct_means.values())) <= 1:
            most_dominant = least_contributing = None
        else:
            most_dominant = max(stats_by_pillar.values(), key=lambda s: s.mean_percentage_contribution).pillar
            least_contributing = min(stats_by_pillar.values(), key=lambda s: s.mean_percentage_contribution).pillar

        return PillarContributionReport(
            n_games=n_games, stats_by_pillar=stats_by_pillar,
            most_dominant_pillar=most_dominant, least_contributing_pillar=least_contributing,
        )
