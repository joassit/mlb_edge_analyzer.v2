"""
Respaldo agnóstico de motor de las 5 tablas de producción (game_analysis,
actual_results, bets, picks, feature_snapshots) a CSV + JSON, más un
integrity_report.json con los chequeos de consistencia que hacen falta
ANTES de dar por bueno cualquier dato histórico para migrar/comparar.

Por qué agnóstico de motor: SQLAlchemy ya abstrae SQLite vs. Postgres --
este script no asume ningún dialecto, solo usa create_engine() con
cualquier URL válida (sqlite:///... o postgresql://...). Así sirve tanto
para respaldar el mlb_edge.db legacy (SQLite, vía --database-url apuntando
al archivo ya descargado) como una futura base de JSA en Neon (Postgres),
sin duplicar código.

Qué NO hace: no escribe nada en la base origen (100% lectura), no toca
config.py ni las variables de entorno del proceso que lo invoca (usa su
propio engine/sesión, nunca el `db.database.engine` global, para no atar
este script al DATABASE_URL con el que arrancó el proceso).

Integrity checks (los 4 que importan antes de migrar, ver runbook):
  - picks_huerfanos: Pick sin ninguna fila de GameAnalysis con el mismo
    (game_pk, game_date) -- un pick sin predicción detrás es evidencia de
    corrupción, no de un caso de negocio válido.
  - game_analysis_sin_feature_snapshot: GameAnalysis sin FeatureSnapshot
    con el mismo (game_pk, game_date) -- CRÍTICO para cualquier intento de
    recalcular el juego sin data leakage (ver FeatureSnapshot en
    db/database.py): sin el snapshot congelado, recalcular exigiría
    volver a golpear la stats API con datos ya contaminados por juegos
    posteriores.
  - bets_huerfanas: Bet sin ningún Pick con el mismo (game_pk, game_date,
    market, selección==side) -- una apuesta real sin la recomendación que
    la originó.
  - fechas_futuras: cualquier fila con game_date posterior a --as-of
    (por defecto, la fecha de hoy) -- señal de reloj mal puesto en el
    proceso que la escribió, o de un game_pk/game_date mal parseado.

Uso:
    python scripts/export_data.py \\
        --database-url "sqlite:///_artifact_legacy/mlb_edge.db" \\
        --output-dir "migration_backups/legacy_20260718"

    # Contra la DB del proceso actual (usa config.DATABASE_URL):
    python scripts/export_data.py --output-dir "migration_backups/legacy_$(date +%Y%m%d)"
"""

import argparse
import json
import os
from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import GameAnalysis, ActualResult, Bet, Pick, FeatureSnapshot

_TABLES = {
    "game_analysis": GameAnalysis,
    "actual_results": ActualResult,
    "bets": Bet,
    "picks": Pick,
    "feature_snapshots": FeatureSnapshot,
}


def _row_to_dict(row) -> dict:
    out = {}
    for col in row.__table__.columns:
        value = getattr(row, col.name)
        if isinstance(value, (datetime, date)):
            value = value.isoformat()
        out[col.name] = value
    return out


def _dump_table(session, model, name: str, output_dir: str) -> list[dict]:
    rows = [_row_to_dict(r) for r in session.query(model).all()]
    with open(os.path.join(output_dir, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1, default=str)

    if rows:
        import csv
        fieldnames = list(rows[0].keys())
        with open(os.path.join(output_dir, f"{name}.csv"), "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    else:
        open(os.path.join(output_dir, f"{name}.csv"), "w").close()

    return rows


def _check_integrity(dumps: dict[str, list[dict]], as_of: str) -> dict:
    ga_keys = {(r["game_pk"], r["game_date"]) for r in dumps["game_analysis"]}
    fs_keys = {(r["game_pk"], r["game_date"]) for r in dumps["feature_snapshots"]}
    pick_keys = {(r["game_pk"], r["game_date"], r["market"], r["selection"]) for r in dumps["picks"]}

    picks_huerfanos = [
        {"game_pk": r["game_pk"], "game_date": r["game_date"], "market": r["market"], "selection": r["selection"]}
        for r in dumps["picks"] if (r["game_pk"], r["game_date"]) not in ga_keys
    ]
    ga_sin_snapshot = [
        {"game_pk": r["game_pk"], "game_date": r["game_date"], "model_version": r["model_version"]}
        for r in dumps["game_analysis"] if (r["game_pk"], r["game_date"]) not in fs_keys
    ]
    bets_huerfanas = [
        {"id": r["id"], "game_pk": r["game_pk"], "game_date": r["game_date"], "market": r["market"], "side": r["side"]}
        for r in dumps["bets"]
        if (r["game_pk"], r["game_date"], r["market"], r["side"]) not in pick_keys
    ]
    fechas_futuras = [
        {"table": name, "game_pk": r["game_pk"], "game_date": r["game_date"]}
        for name, rows in dumps.items() if name != "actual_results" or True
        for r in rows
        if "game_date" in r and r["game_date"] and r["game_date"] > as_of
    ]

    return {
        "as_of": as_of,
        "row_counts": {name: len(rows) for name, rows in dumps.items()},
        "picks_huerfanos": {"n": len(picks_huerfanos), "detalle": picks_huerfanos},
        "game_analysis_sin_feature_snapshot": {"n": len(ga_sin_snapshot), "detalle": ga_sin_snapshot},
        "bets_huerfanas": {"n": len(bets_huerfanas), "detalle": bets_huerfanas},
        "fechas_futuras": {"n": len(fechas_futuras), "detalle": fechas_futuras},
        "limpio": (
            len(picks_huerfanos) == 0
            and len(ga_sin_snapshot) == 0
            and len(bets_huerfanas) == 0
            and len(fechas_futuras) == 0
        ),
    }


def export_data(database_url: str, output_dir: str, as_of: str | None = None) -> dict:
    as_of = as_of or date.today().strftime("%Y-%m-%d")
    os.makedirs(output_dir, exist_ok=True)

    engine = create_engine(database_url)
    session = sessionmaker(bind=engine)()
    try:
        dumps = {
            name: _dump_table(session, model, name, output_dir)
            for name, model in _TABLES.items()
        }
    finally:
        session.close()

    report = _check_integrity(dumps, as_of)
    report["database_url"] = database_url
    report["exported_at"] = datetime.utcnow().isoformat() + "Z"
    with open(os.path.join(output_dir, "integrity_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)

    print(f"Exportado a {output_dir}/ -- conteos: {report['row_counts']}")
    print(
        f"Integridad: picks_huerfanos={report['picks_huerfanos']['n']} "
        f"game_analysis_sin_feature_snapshot={report['game_analysis_sin_feature_snapshot']['n']} "
        f"bets_huerfanas={report['bets_huerfanas']['n']} "
        f"fechas_futuras={report['fechas_futuras']['n']}"
    )
    print("✅ Sin hallazgos -- datos consistentes." if report["limpio"] else "⚠️ Hay hallazgos, revisar integrity_report.json.")
    return report


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url", default=None,
        help="URL de SQLAlchemy a respaldar (ej. sqlite:///_artifact_legacy/mlb_edge.db). "
             "Si se omite, usa config.DATABASE_URL del proceso actual.",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Carpeta destino para CSV/JSON + integrity_report.json. "
             "Por defecto migration_backups/legacy_<hoy>.",
    )
    parser.add_argument(
        "--as-of", default=None,
        help="Fecha (YYYY-MM-DD) contra la que se considera 'futura' una game_date. Por defecto, hoy.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    db_url = args.database_url
    if db_url is None:
        from config import DATABASE_URL
        db_url = DATABASE_URL
    out_dir = args.output_dir or f"migration_backups/legacy_{date.today().strftime('%Y%m%d')}"
    export_data(db_url, out_dir, args.as_of)
