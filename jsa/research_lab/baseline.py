"""Baseline real del Game Flow Research Lab -- nunca hardcodeado, siempre
leido de los registries ya poblados (`gate_registry` via Fase 6,
`calibration_registry` via Fase 4). Toda hipotesis nueva se compara
contra ESTOS numeros, nunca contra una version anterior de si misma ni
contra un numero inventado. Ver README.md de este paquete."""

from __future__ import annotations

from jsa.config import PRODUCTION_CALIBRATION_ID
from jsa.registries import db as registries_db

GATE_IDS_BY_MARKET: dict[str, str] = {
    "moneyline_home": "gate-moneyline_home-v1",
    "moneyline_away": "gate-moneyline_away-v1",
}


def load_gate_baseline(engine) -> dict[str, dict]:
    """Ultima fila real de `gate_registry` por mercado con modelo --
    thresholds, accuracy_wilson_ci_low/high, coverage_pct/n, status. Vacio
    si `gate_threshold_sweep --sync-to-registries` todavia no corrio."""
    rows = registries_db.latest_by_id(registries_db.get_engine(engine) if isinstance(engine, str) else engine, registries_db.gate_registry, "gate_id")
    return {market: rows[gate_id] for market, gate_id in GATE_IDS_BY_MARKET.items() if gate_id in rows}


def load_calibration_baseline(engine) -> dict | None:
    """Ultima fila real de `calibration_registry` para la curva de
    produccion actual -- loso_brier/log_loss/accuracy/ece/mce. `None` si
    `calibrate --sync-to-registries` todavia no corrio."""
    eng = registries_db.get_engine(engine) if isinstance(engine, str) else engine
    rows = registries_db.latest_by_id(eng, registries_db.calibration_registry, "calibration_id")
    return rows.get(PRODUCTION_CALIBRATION_ID)


def load_full_baseline(engine) -> dict:
    """Punto unico de entrada -- todo lo que el laboratorio necesita como
    referencia real antes de evaluar cualquier hipotesis nueva."""
    eng = registries_db.get_engine(engine) if isinstance(engine, str) else engine
    return {
        "gate": load_gate_baseline(eng),
        "calibration": load_calibration_baseline(eng),
    }
