"""Aisla el pipeline de produccion (`jsa/main.py`) de cualquier motor
experimental/historico/legado -- mismo principio que
`mlb_edge_analyzer.v2/tests/test_historical_isolation.py`, y coincide con
la Seccion 2 del spec JSA v3.0 ("Experiment Engine corre en paralelo, fuera
del flujo de evaluacion en vivo"). Ahora que `jsa/historical/` y
`jsa/legacy/` existen de verdad (no solo como nombres futuros), este test
deja de ser una promesa y pasa a ser una garantia real verificada en CI."""

from __future__ import annotations

import pathlib

_JSA_ROOT = pathlib.Path(__file__).resolve().parent.parent

FORBIDDEN_IMPORTS = (
    "experiment_engine", "historical_engine", "backtest",
    "jsa.historical", "jsa.legacy", "from jsa.historical", "from jsa.legacy", "from .historical", "from .legacy",
)

# Subconjunto seguro para chequear codigo real (no docstrings en prosa que
# mencionan "backtest"/"experiment_engine" como palabras normales al
# explicar el diseño) -- solo formas de import calificadas, nunca
# ambiguas con texto explicativo.
FORBIDDEN_IMPORT_STATEMENTS = ("jsa.historical", "jsa.legacy", "from .historical", "from .legacy")


def test_main_does_not_import_experimental_engines():
    main_source = _JSA_ROOT.joinpath("main.py").read_text()
    for forbidden in FORBIDDEN_IMPORTS:
        assert forbidden not in main_source, f"jsa/main.py no debe referenciar '{forbidden}' -- ver Seccion 2 del spec"


def test_orchestrator_is_pure_no_io_imports():
    """engine/orchestrator.py es la funcion unica de evaluacion
    reutilizable en vivo y en un futuro backtest (Seccion 2) -- no debe
    importar `requests` ni los data_sources (que hacen I/O de red), ni
    nada de `historical`/`legacy`."""
    orchestrator_source = _JSA_ROOT.joinpath("engine", "orchestrator.py").read_text()
    assert "import requests" not in orchestrator_source
    assert "data_sources" not in orchestrator_source
    for forbidden in FORBIDDEN_IMPORT_STATEMENTS:
        assert forbidden not in orchestrator_source


def test_no_engine_module_imports_historical_or_legacy():
    """Ningun modulo de `jsa/engine/` (el motor de produccion en vivo)
    puede importar de `jsa/historical/` ni `jsa/legacy/` -- la relacion de
    dependencia va en un solo sentido: historical/legacy pueden importar
    de engine/domain (para reusar la logica pura), nunca al reves."""
    engine_dir = _JSA_ROOT / "engine"
    for py_file in engine_dir.rglob("*.py"):
        source = py_file.read_text()
        for forbidden in ("jsa.historical", "jsa.legacy", "from .historical", "from .legacy"):
            assert forbidden not in source, f"{py_file.relative_to(_JSA_ROOT)} no debe importar '{forbidden}'"


def test_no_analytics_module_imports_historical_or_legacy():
    """`jsa/analytics/` (agregacion pura, ej. PillarContributionAnalyzer)
    esta pensado para ser importable desde produccion algun dia -- debe
    quedar tan limpio de `historical`/`legacy` como `jsa/engine/`."""
    analytics_dir = _JSA_ROOT / "analytics"
    for py_file in analytics_dir.rglob("*.py"):
        source = py_file.read_text()
        for forbidden in FORBIDDEN_IMPORT_STATEMENTS:
            assert forbidden not in source, f"{py_file.relative_to(_JSA_ROOT)} no debe importar '{forbidden}'"


def test_legacy_readme_declares_isolation_rule():
    """`jsa/legacy/README.md` debe declarar explicitamente la regla de
    aislamiento -- documentacion viva, no solo un test escondido."""
    readme = _JSA_ROOT.joinpath("legacy", "README.md").read_text()
    assert "jsa/main.py" in readme
    assert "engine/orchestrator.py" in readme


def test_historical_and_legacy_never_write_to_production_storage_module():
    """`jsa/historical/db.py` es la unica fuente de persistencia para
    datos de juego historicos -- nunca debe importar
    `jsa/storage/database.py` (produccion), solo `jsa/storage/dialect_utils.py`
    (la utilidad compartida y sin estado)."""
    historical_db_source = _JSA_ROOT.joinpath("historical", "db.py").read_text()
    assert "storage.database" not in historical_db_source
    assert "storage.dialect_utils" in historical_db_source
