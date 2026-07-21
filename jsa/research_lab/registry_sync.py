"""Persiste el resultado de una hipotesis del laboratorio en
`experiment_registry` -- reusa la tabla ya existente (append-only, sin
migracion de schema) con `decision="retained_in_lab"` /
`"rejected_no_improvement"`, nunca `"promoted_active"` (esa decision
sigue reservada para cuando una hipotesis supera el Scientific Validation
Pipeline completo y se integra de verdad al modelo de produccion -- ver
README.md de este paquete). Escribir aca nunca cambia el comportamiento
de `jsa/main.py`: `experiment_registry` es evidencia, no configuracion
activa (a diferencia de `gate_registry`/`calibration_registry`/
`rule_registry`)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date

from jsa import config as production_config
from jsa.registries import db as registries_db
from jsa.research_lab.hypothesis_report import HypothesisReport


def append_hypothesis_result(engine, report: HypothesisReport, *, seasons: list[int]) -> None:
    decision = "retained_in_lab" if report.retained_in_lab else "rejected_no_improvement"
    registries_db.append(
        engine, registries_db.experiment_registry,
        experiment_id=report.hypothesis_id,
        description=f"Game Flow Research Lab -- {report.module_name} sobre {report.market} ({report.retention_reason})",
        base_model_version=production_config.MODEL_VERSION,
        feature_set=[], weights={}, rules=[],
        date_range={"seasons": sorted(seasons)},
        metrics_requested=[
            "accuracy", "roc_auc", "brier", "log_loss", "ece", "roi", "lift_by_edge", "gate_coverage", "feature_importance",
        ],
        baseline_comparison=["Game Flow Research Lab baseline (gate_registry + calibration_registry reales, ver research_lab/baseline.py)"],
        benchmarking_result=asdict(report),
        decision=decision,
    )
