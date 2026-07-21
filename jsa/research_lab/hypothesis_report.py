"""Plantilla de reporte OBLIGATORIA para toda hipotesis del Game Flow
Research Lab -- las 9 metricas minimas acordadas con el usuario
(2026-07-21), mas el criterio de permanencia: una hipotesis se queda en
el laboratorio aunque no alcance 70% de accuracy si demuestra una mejora
ESTADISTICAMENTE CONSISTENTE sobre el baseline en 1+ metrica relevante.

"Estadisticamente consistente" tiene una definicion precisa, nunca a ojo:
el intervalo de bootstrap PAREADO de
`jsa.historical.significance.paired_bootstrap_ci()` sobre el delta de esa
metrica excluye 0 al 90% -- misma alpha, misma funcion ya usada en Fase 3
(nunca reimplementada aqui). Esa funcion opera sobre error cuadratico
(Brier); para otras metricas (accuracy, log loss, ECE, ROC-AUC) la
significancia se establece cuando exista una hipotesis real que las mida
-- no se generaliza la funcion de antemano sin un caso real que lo
ejercite (ver README.md, tabla de fuentes por metrica).

Fuente real de cada campo, nunca fabricada -- ver README.md de este
paquete para el detalle completo (que esta listo vs. que es un gap real
como `delta_roi`, bloqueado por falta de cuotas de mercado ingeridas)."""

from __future__ import annotations

from dataclasses import dataclass, field

from jsa.historical.significance import paired_bootstrap_ci

_ALPHA = 0.10  # misma alpha que significance.py, nunca otro numero sin justificar


@dataclass
class HypothesisReport:
    hypothesis_id: str
    module_name: str
    market: str
    n_games: int

    delta_accuracy: float | None = None
    delta_roc_auc: float | None = None
    delta_brier: float | None = None
    delta_log_loss: float | None = None
    delta_ece: float | None = None
    delta_roi: float | None = None  # None hasta que haya cuotas de mercado reales ingeridas
    delta_lift_by_edge: list[float] | None = None
    delta_gate_coverage: float | None = None
    feature_importance: dict[str, float] | None = None

    significance: dict[str, dict] = field(default_factory=dict)  # metric_name -> resultado de paired_bootstrap_ci
    retained_in_lab: bool = False
    retention_reason: str = ""


def evaluate_brier_significance(baseline_pairs: list[tuple[float, int]], hypothesis_pairs: list[tuple[float, int]]) -> dict | None:
    """CI de bootstrap pareado sobre el delta de Brier entre baseline e
    hipotesis -- reusa `paired_bootstrap_ci` tal cual, nunca reimplementada
    (alpha=0.10 ya fijo adentro de esa funcion, mismo valor que `_ALPHA`
    aca). `pairs` son (prediccion, resultado_real) alineados juego a juego,
    mismo formato que el resto de `historical/`."""
    return paired_bootstrap_ci(baseline_pairs, hypothesis_pairs)


def decide_retention(significance: dict[str, dict]) -> tuple[bool, str]:
    """Seccion 6 del acuerdo: la hipotesis se queda en el laboratorio si
    AL MENOS UNA metrica tiene una mejora estadisticamente consistente
    (CI de bootstrap que no cruza 0, en la direccion de mejora) -- no
    exige 70% de accuracy ni mejora simultanea en todas las metricas."""
    improved = [name for name, result in significance.items() if result and result.get("significant")]
    if improved:
        return True, f"Mejora estadisticamente consistente en: {', '.join(sorted(improved))}"
    return False, "Ninguna metrica mostro mejora estadisticamente consistente sobre el baseline"
