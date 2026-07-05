"""
Verifica que los hallazgos V4-V11 de la auditoría exhaustiva v2 (ver
self_audit_v2.py) quedaron efectivamente cerrados. Distinto de
self_audit_v2.py, que audita el estado GENERAL del código en un momento
dado: este script re-verifica, uno por uno, los defectos puntuales que se
pidió corregir en esta fase (excepciones de red no capturadas, config no
congelada, .gitignore incompleto, cachés sin lock, omisiones sin log,
probabilidades sin validar, dependencias sin pin, falta de índice).

Uso:
    python self_audit_v2_fixed.py
"""

import ast
import os
import subprocess
import sys
import threading

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_ICONS = {"OK": "✅", "FAIL": "❌"}
_results: list[tuple[str, str]] = []


def check(status: str, message: str) -> None:
    _results.append((status, message))
    print(f"{_ICONS[status]} {message}")


def _function_wraps_network_call_in_try(filepath: str, func_name: str) -> bool:
    """True si `func_name` tiene al menos un bloque try/except que envuelve
    una llamada *.get(...) -- verificación estructural (AST), no de texto,
    para no dar un falso positivo con un try/except en otra parte de la
    función que no cubra la llamada de red."""
    with open(filepath, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=filepath)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Try):
                    for sub in ast.walk(stmt):
                        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                                and sub.func.attr == "get"):
                            return True
            return False
    return False  # función no encontrada -- no cuenta como corregida


def check_v4_network_exceptions() -> bool:
    targets = [
        ("data/stats.py", ["get_pitcher_era", "get_pitcher_era_ip", "get_team_ops", "get_league_ops"]),
        ("data/mlb_api.py", ["get_schedule", "get_game_result"]),
    ]
    missing = []
    for rel_path, func_names in targets:
        path = os.path.join(REPO_ROOT, rel_path)
        for fn in func_names:
            if not _function_wraps_network_call_in_try(path, fn):
                missing.append(f"{rel_path}::{fn}")
    ok = not missing
    check("OK" if ok else "FAIL",
          "V4 Excepciones de red: todas las funciones envuelven su llamada HTTP en try/except"
          if ok else f"V4 Excepciones de red: faltan {missing}")
    return ok


def check_v5_feature_snapshot_frozen() -> bool:
    from db.database import FeatureSnapshot
    required = ["park_factor_weight", "weather_correction", "starter_weight", "home_field_advantage"]
    missing = [f for f in required if not hasattr(FeatureSnapshot, f)]
    ok = not missing
    check("OK" if ok else "FAIL",
          "V5 FeatureSnapshot congelado: las 4 columnas de config están presentes"
          if ok else f"V5 FeatureSnapshot congelado: faltan columnas {missing}")
    return ok


def check_v6_gitignore_and_tracked_files() -> bool:
    gitignore_path = os.path.join(REPO_ROOT, ".gitignore")
    with open(gitignore_path, encoding="utf-8") as f:
        gitignore_ok = "reports/*.csv" in f.read()

    result = subprocess.run(["git", "ls-files", "reports/"], capture_output=True, text=True, cwd=REPO_ROOT)
    tracked_csvs = [l for l in result.stdout.splitlines() if l.endswith(".csv")]
    ok = gitignore_ok and not tracked_csvs
    check("OK" if ok else "FAIL",
          f"V6 .gitignore/reports: patrón reports/*.csv presente={gitignore_ok}, "
          f"CSVs trackeados={tracked_csvs or 'ninguno'}")
    return ok


def check_v7_cache_locks() -> bool:
    import data.stats as stats_mod
    ok = hasattr(stats_mod, "_cache_lock") and isinstance(stats_mod._cache_lock, type(threading.Lock()))
    check("OK" if ok else "FAIL", f"V7 Lock de cachés: _cache_lock {'presente' if ok else 'AUSENTE'} en data/stats.py")
    return ok


def check_v8_logging_on_skip() -> bool:
    path = os.path.join(REPO_ROOT, "main.py")
    with open(path, encoding="utf-8") as f:
        content = f.read()
    count = content.count("logger.warning")
    ok = count >= 3
    check("OK" if ok else "FAIL", f"V8 Logging de omisiones: {count} llamada(s) a logger.warning en main.py (mínimo 3)")
    return ok


def check_v9_probability_validation() -> bool:
    from tracking.results_tracker import validate_probabilities
    ok = callable(validate_probabilities)
    check("OK" if ok else "FAIL", f"V9 Validación de rango: validate_probabilities() {'existe' if ok else 'AUSENTE'}")
    return ok


def check_v10_requirements_pinned() -> bool:
    path = os.path.join(REPO_ROOT, "requirements.txt")
    with open(path, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    unpinned = [l for l in lines if "==" not in l]
    ok = not unpinned
    check("OK" if ok else "FAIL",
          f"V10 Dependencias pinned: {len(lines) - len(unpinned)}/{len(lines)} con == "
          + ("" if ok else f"-- sin pin: {unpinned}"))
    return ok


def check_v11_game_date_index() -> bool:
    from sqlalchemy import inspect
    from db.database import engine, GameAnalysis, init_db
    init_db()
    inspector = inspect(engine)
    indexes = [ix["name"] for ix in inspector.get_indexes(GameAnalysis.__tablename__)]
    ok = any("game_date" in ix for ix in indexes)
    check("OK" if ok else "FAIL", f"V11 Índice game_date: {'presente' if ok else 'AUSENTE'} (índices: {indexes})")
    return ok


def run_all_checks() -> bool:
    print(f"\n{'=' * 70}\nSELF-AUDIT V2 FIXED -- cierre de hallazgos V4-V11\n{'=' * 70}\n")
    checks = [
        check_v4_network_exceptions,
        check_v5_feature_snapshot_frozen,
        check_v6_gitignore_and_tracked_files,
        check_v7_cache_locks,
        check_v8_logging_on_skip,
        check_v9_probability_validation,
        check_v10_requirements_pinned,
        check_v11_game_date_index,
    ]
    for c in checks:
        try:
            c()
        except Exception as e:
            check("FAIL", f"{c.__name__}: excepción durante la verificación -- {e}")

    n_ok = sum(1 for s, _ in _results if s == "OK")
    n_fail = sum(1 for s, _ in _results if s == "FAIL")
    print(f"\n{'=' * 70}\nRESUMEN\n{'=' * 70}")
    print(f"✅ {n_ok}   ❌ {n_fail}\n")
    return n_fail == 0


if __name__ == "__main__":
    success = run_all_checks()
    sys.exit(0 if success else 1)
