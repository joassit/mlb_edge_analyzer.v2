"""
Auto-auditoría contra la base de datos y el código. Imprime ✅/❌/⚠️/➖ por
cada punto y un resumen final.

➖ (N/A) se usa cuando el punto depende de juegos de HOY en game_analysis y
no hay ninguno -- no es lo mismo que un ❌, es la ausencia de una corrida
real de main.py contra la API en vivo.

Uso:
    python self_audit.py
"""

import os
import subprocess
import sys
from datetime import date

from sqlalchemy import inspect, text

from config import DATABASE_URL
from db.database import SessionLocal, GameAnalysis, engine, init_db

_results: list[tuple[str, str]] = []
_ICONS = {"OK": "✅", "FAIL": "❌", "WARN": "⚠️", "NA": "➖"}


def check(status: str, message: str) -> None:
    _results.append((status, message))
    print(f"{_ICONS[status]} {message}")


def run() -> bool:
    init_db()
    today = date.today().strftime("%Y-%m-%d")

    session = SessionLocal()
    try:
        today_rows = session.query(GameAnalysis).filter(GameAnalysis.game_date == today).all()
    finally:
        session.close()

    print(f"\n{'=' * 70}\nAUTO-AUDITORÍA — {today}\n{'=' * 70}\n")

    # 1. Probabilidades vivas
    if not today_rows:
        check("NA", "1. Probabilidades vivas: sin juegos de hoy en game_analysis.")
    else:
        alive = [r for r in today_rows if r.home_model_prob != 0.5]
        pct = len(alive) / len(today_rows) * 100
        check("OK" if pct == 100 else "FAIL",
              f"1. Probabilidades vivas: {pct:.0f}% de {len(today_rows)} juego(s) con home_model_prob != 0.5")

    # 2. Suma a 1
    if not today_rows:
        check("NA", "2. Suma a 1: sin juegos de hoy que verificar.")
    else:
        bad = [r for r in today_rows
               if abs((r.home_model_prob or 0) + (r.away_model_prob or 0) - 1) >= 1e-3]
        check("OK" if not bad else "FAIL",
              f"2. Suma a 1: {len(today_rows) - len(bad)}/{len(today_rows)} juego(s) con "
              f"home_model_prob+away_model_prob=1 (±1e-3)")

    # 3. Idempotencia
    session = SessionLocal()
    try:
        dupes = session.execute(text(
            "SELECT game_pk, game_date, model_version, COUNT(*) c FROM game_analysis "
            "GROUP BY game_pk, game_date, model_version HAVING c > 1"
        )).fetchall()
    finally:
        session.close()
    check("OK" if not dupes else "FAIL",
          f"3. Idempotencia: {len(dupes)} grupo(s) duplicado(s) por "
          f"(game_pk, game_date, model_version) -- esperado 0")

    # 4. Sin código muerto
    dead_files = [
        "sync_results.py", "audit_live.py", "main.py.backup",
        "audit_today.py", "backtest_model.py", "audit_today.p",
        "clean_test.py", "test_insert.py",
    ]
    still_present = [f for f in dead_files if os.path.exists(f)]

    grep = subprocess.run(
        ["grep", "-rn", "sqlite3.connect", "--include=*.py", "."],
        capture_output=True, text=True,
    )
    # tests/ y db/migrate_v05.py (rebuild de esquema) son la excepción
    # explícita permitida; tracking/results_tracker.py solo lo MENCIONA en
    # un docstring explicando qué reemplazó, no lo ejecuta -- verificado a
    # mano (ver commit de esta fase).
    offending_lines = [
        line for line in grep.stdout.splitlines()
        if "/tests/" not in line
        and "tracking/results_tracker.py" not in line
        and "self_audit.py" not in line  # este propio script busca el string, no lo ejecuta
    ]
    ok = not still_present and not offending_lines
    check("OK" if ok else "FAIL",
          f"4. Sin código muerto: archivos vivos={still_present or 'ninguno'}; "
          f"sqlite3.connect fuera de tests/migración={len(offending_lines)}")

    # 5. Constraint activo
    inspector = inspect(engine)
    unique_names = {uc["name"] for uc in inspector.get_unique_constraints("game_analysis")}
    check("OK" if "uq_pred" in unique_names else "FAIL",
          f"5. Constraint uq_pred: {'presente' if 'uq_pred' in unique_names else 'AUSENTE -- correr db/migrate_v05.py'}")

    # 6. Coherencia Skellam vs heurístico
    if not today_rows:
        check("NA", "6. Coherencia Skellam vs heurístico: sin juegos de hoy.")
    else:
        pairs = [(r.game_pk, r.home_model_prob, r.home_skellam_prob) for r in today_rows
                 if r.home_model_prob is not None and r.home_skellam_prob is not None]
        if not pairs:
            check("NA", "6. Coherencia Skellam vs heurístico: faltan datos de alguno de los dos modelos.")
        else:
            diffs = [abs(h - s) for _, h, s in pairs]
            mean_abs_diff = sum(diffs) / len(diffs)
            flagged = [pk for pk, h, s in pairs if abs(h - s) > 0.15]
            check("WARN" if flagged else "OK",
                  f"6. Coherencia Skellam vs heurístico: diferencia media absoluta={mean_abs_diff:.3f}; "
                  f"{len(flagged)} juego(s) discrepan >0.15: {flagged}")

    # 7. Sanidad de proyecciones
    if not today_rows:
        check("NA", "7. Sanidad de proyecciones: sin juegos de hoy.")
    else:
        bad = [r.game_pk for r in today_rows
               if r.away_proj_runs is None or r.home_proj_runs is None
               or not (2.0 <= r.away_proj_runs <= 8.0) or not (2.0 <= r.home_proj_runs <= 8.0)]
        check("OK" if not bad else "FAIL",
              f"7. Proyecciones en [2.0, 8.0] y no NULL: {len(today_rows) - len(bad)}/{len(today_rows)} OK; "
              f"problemáticos: {bad}")

    # 8. WAL activo
    if DATABASE_URL.startswith("sqlite"):
        with engine.connect() as conn:
            mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        check("OK" if mode == "wal" else "FAIL", f"8. journal_mode: {mode}")
    else:
        check("NA", "8. WAL: no aplica (DATABASE_URL no es SQLite)")

    # 9. Tests
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q"], capture_output=True, text=True)
    tail = [line for line in proc.stdout.splitlines() if line.strip()]
    last_line = tail[-1] if tail else "(sin salida)"
    check("OK" if proc.returncode == 0 else "FAIL", f"9. Tests: {last_line}")

    print(f"\n{'=' * 70}\nRESUMEN DE AUTO-AUDITORÍA\n{'=' * 70}")
    n_ok = sum(1 for s, _ in _results if s == "OK")
    n_fail = sum(1 for s, _ in _results if s == "FAIL")
    n_warn = sum(1 for s, _ in _results if s == "WARN")
    n_na = sum(1 for s, _ in _results if s == "NA")
    print(f"✅ {n_ok}   ❌ {n_fail}   ⚠️  {n_warn}   ➖ N/A {n_na}\n")

    if n_na:
        print(
            "Nota: los puntos marcados ➖ N/A dependen de juegos de HOY en game_analysis.\n"
            "Este entorno no tiene salida de red hacia la MLB Stats API (bloqueada por\n"
            "política del sandbox) -- no hay datos reales de hoy para auditar. No es una\n"
            "falla del código: es la ausencia de una corrida real de main.py, que debe\n"
            "hacerse en un entorno con acceso a internet para completar esta auditoría.\n"
        )

    return n_fail == 0


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
