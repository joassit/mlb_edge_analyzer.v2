"""Aisla el pipeline de produccion (`jsa/main.py`) de cualquier motor
experimental/historico futuro -- mismo principio que
`mlb_edge_analyzer.v2/tests/test_historical_isolation.py`, y coincide con
la Seccion 2 del spec JSA v3.0 ("Experiment Engine corre en paralelo, fuera
del flujo de evaluacion en vivo"). Cuando exista un futuro
`jsa/experiment_engine/` (Fase 3 del roadmap), este test debe seguir
pasando -- si falla, es la senal de que alguien conecto el motor de
experimentos directo al pipeline en vivo."""

from __future__ import annotations

import pathlib

FORBIDDEN_IMPORTS = ("experiment_engine", "historical_engine", "backtest")


def test_main_does_not_import_experimental_engines():
    main_source = pathlib.Path(__file__).resolve().parent.parent.joinpath("main.py").read_text()
    for forbidden in FORBIDDEN_IMPORTS:
        assert forbidden not in main_source, f"jsa/main.py no debe referenciar '{forbidden}' -- ver Seccion 2 del spec"


def test_orchestrator_is_pure_no_io_imports():
    """engine/orchestrator.py es la funcion unica de evaluacion
    reutilizable en vivo y en un futuro backtest (Seccion 2) -- no debe
    importar `requests` ni los data_sources (que hacen I/O de red)."""
    orchestrator_source = (
        pathlib.Path(__file__).resolve().parent.parent.joinpath("engine", "orchestrator.py").read_text()
    )
    assert "import requests" not in orchestrator_source
    assert "data_sources" not in orchestrator_source
