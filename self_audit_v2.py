"""
Auto-verificación de la auditoría exhaustiva (Secciones 5-12). Distinto de
self_audit.py (que audita el ESTADO de los datos en game_analysis): este
script audita PROPIEDADES ESTÁTICAS DEL CÓDIGO -- timeouts, índices,
secretos en git, código muerto, manejo de excepciones.

Uso:
    python self_audit_v2.py
"""

import ast
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_results: list[tuple[str, str]] = []
_ICONS = {"OK": "✅", "FAIL": "❌", "WARN": "⚠️"}


def check(status: str, message: str) -> None:
    _results.append((status, message))
    print(f"{_ICONS[status]} {message}")


def _iter_get_calls_without_timeout(filepath: str):
    """AST: encuentra llamadas *.get(...) sobre session/_session/requests que
    no tengan un argumento keyword `timeout=`. Evita falsos negativos de un
    grep de una sola línea contra llamadas multi-línea."""
    with open(filepath, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=filepath)

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "get"):
            continue
        # Filtra solo llamadas .get() sobre objetos tipo sesión HTTP -- no
        # queremos falsos positivos de dict.get()/os.environ.get().
        callee_name = ""
        if isinstance(func.value, ast.Name):
            callee_name = func.value.id
        if callee_name not in {"session", "_session", "requests"}:
            continue
        has_timeout = any(kw.arg == "timeout" for kw in node.keywords)
        if not has_timeout:
            offenders.append(node.lineno)
    return offenders


def check_section_5_timeouts() -> bool:
    """Verifica que TODAS las llamadas *.get() sobre sesiones HTTP tengan timeout=."""
    files = ["data/mlb_api.py", "data/stats.py", "data/weather.py", "data/odds_api.py"]
    all_ok = True
    for rel_path in files:
        path = os.path.join(REPO_ROOT, rel_path)
        if not os.path.exists(path):
            continue
        offenders = _iter_get_calls_without_timeout(path)
        if offenders:
            all_ok = False
            check("FAIL", f"5.1 Timeouts: {rel_path} tiene llamada(s) sin timeout= en línea(s) {offenders}")
    if all_ok:
        check("OK", "5.1 Timeouts: todas las llamadas *.get() sobre sesiones HTTP en data/ tienen timeout=")
    return all_ok


def check_section_5_retry_config() -> bool:
    """Verifica que data/http.py, data/weather.py tengan Retry con status_forcelist."""
    ok = True
    for rel_path in ["data/http.py", "data/weather.py"]:
        path = os.path.join(REPO_ROOT, rel_path)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        has_retry = "Retry(" in content
        has_forcelist = "status_forcelist" in content
        if not (has_retry and has_forcelist):
            ok = False
            check("FAIL", f"5.2 Retry: {rel_path} no tiene Retry()/status_forcelist configurado")
    if ok:
        check("OK", "5.2 Retry: data/http.py y data/weather.py tienen Retry + status_forcelist")
    return ok


def check_section_5_generic_excepts() -> bool:
    """Lista `except Exception` / `except:` desnudos fuera de tests/ -- no
    son necesariamente un error, pero cada uno debe revisarse a mano."""
    result = subprocess.run(
        ["grep", "-rn", "-E", "except Exception|except:", "--include=*.py", "."],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    offenders = [l for l in result.stdout.splitlines() if "/tests/" not in l and "self_audit" not in l]
    check("WARN" if offenders else "OK",
          f"5.6 Excepciones genéricas fuera de tests/: {len(offenders)} -- {offenders}")
    return True  # informativo, no bloqueante


def check_section_8_indexes() -> bool:
    """Verifica si existe un índice cuyo primer campo sea game_date en
    game_analysis -- el UniqueConstraint uq_pred (game_pk, game_date,
    model_version) NO sirve como índice de rango para queries que filtran
    solo por game_date (game_pk no está fijo)."""
    sys.path.insert(0, REPO_ROOT)
    from sqlalchemy import inspect
    from db.database import engine, init_db

    init_db()
    inspector = inspect(engine)
    indexes = inspector.get_indexes("game_analysis")
    unique_constraints = inspector.get_unique_constraints("game_analysis")

    leftmost_game_date = any(idx["column_names"][0] == "game_date" for idx in indexes if idx["column_names"])
    uq_leftmost_game_date = any(
        uc["column_names"][0] == "game_date" for uc in unique_constraints if uc["column_names"]
    )
    has_usable_index = leftmost_game_date or uq_leftmost_game_date

    if has_usable_index:
        detail = "sí"
    else:
        detail = "NO -- get_predictions_without_result() hace full table scan en SQLite/Postgres"
    check("OK" if has_usable_index else "WARN",
          f"8.1 Índice utilizable para queries por game_date: {detail}")
    return has_usable_index


def check_section_9_secrets_in_gitignore() -> bool:
    gitignore_path = os.path.join(REPO_ROOT, ".gitignore")
    with open(gitignore_path, encoding="utf-8") as f:
        patterns = {line.strip() for line in f if line.strip() and not line.startswith("#")}

    required = {"*.db", "logs/", ".env"}
    missing = required - patterns
    if missing:
        check("FAIL", f"9.4 .gitignore: faltan patrones {missing}")

    # Chequeo adicional, más allá de lo pedido: reports/*.csv NO está en la
    # lista requerida original, pero es un hallazgo real -- se reporta aparte.
    reports_ignored = any("reports" in p and "csv" in p for p in patterns)
    if not reports_ignored:
        check("WARN", "9.4 .gitignore: reports/*.csv NO está excluido -- ver hallazgo de reporte_20260703.csv commiteado")

    return not missing


def check_section_9_tracked_sensitive_files() -> bool:
    """Verifica si hay .db/.env o CSVs de reportes generados TRACKEADOS en git."""
    result = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=REPO_ROOT)
    tracked = result.stdout.splitlines()
    offenders = [f for f in tracked if f.endswith((".db", ".env")) or f.startswith("reports/reporte_") or f.startswith("reports/picks_")]
    check("FAIL" if offenders else "OK",
          f"9.4 Archivos generados trackeados en git: {offenders or 'ninguno'}")
    return not offenders


def check_section_9_pinned_dependencies() -> bool:
    req_path = os.path.join(REPO_ROOT, "requirements.txt")
    with open(req_path, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    unpinned = [l for l in lines if "==" not in l]
    check("WARN" if unpinned else "OK",
          f"9.7 Dependencias sin pin exacto (usan >=): {len(unpinned)}/{len(lines)} -- {unpinned}")
    return not unpinned


def check_section_11_dead_files() -> bool:
    dead_files = ["sync_results.py", "audit_live.py", "main.py.backup", "audit_today.py",
                  "backtest_model.py", "audit_today.p", "clean_test.py", "test_insert.py"]
    still_present = [f for f in dead_files if os.path.exists(os.path.join(REPO_ROOT, f))]
    check("OK" if not still_present else "FAIL",
          f"11.4/5.2 Código muerto de auditorías anteriores: {still_present or 'ninguno presente'}")
    return not still_present


def check_section_12_db_url_leak_on_direct_run() -> bool:
    """db/database.py imprime DATABASE_URL completo (con password si es
    Postgres) cuando se corre `python db/database.py` directo."""
    path = os.path.join(REPO_ROOT, "db/database.py")
    with open(path, encoding="utf-8") as f:
        content = f.read()
    leaks = 'print(f"Base de datos lista en: {DATABASE_URL}")' in content
    check("WARN" if leaks else "OK",
          f"9.6/12 db/database.py imprime DATABASE_URL sin enmascarar al correrse directo: {leaks}")
    return not leaks


def run_all_checks() -> dict:
    print(f"\n{'=' * 70}\nSELF-AUDIT V2 -- verificación estática de código\n{'=' * 70}\n")
    results = {}
    results["5.1_timeouts"] = check_section_5_timeouts()
    results["5.2_retry"] = check_section_5_retry_config()
    results["5.6_generic_excepts"] = check_section_5_generic_excepts()
    results["8.1_indexes"] = check_section_8_indexes()
    results["9.4_gitignore"] = check_section_9_secrets_in_gitignore()
    results["9.4_tracked_sensitive"] = check_section_9_tracked_sensitive_files()
    results["9.7_pinned_deps"] = check_section_9_pinned_dependencies()
    results["11.4_dead_files"] = check_section_11_dead_files()
    results["12_db_url_leak"] = check_section_12_db_url_leak_on_direct_run()

    n_ok = sum(1 for s, _ in _results if s == "OK")
    n_fail = sum(1 for s, _ in _results if s == "FAIL")
    n_warn = sum(1 for s, _ in _results if s == "WARN")

    print(f"\n{'=' * 70}\nRESUMEN\n{'=' * 70}")
    print(f"✅ {n_ok}   ❌ {n_fail}   ⚠️  {n_warn}\n")
    return results


if __name__ == "__main__":
    run_all_checks()
