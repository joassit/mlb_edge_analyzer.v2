"""
Migración v0.5: agrega `model_version` al UniqueConstraint de
`game_analysis` (queda `uq_pred` sobre game_pk+game_date+model_version).

SQLite no soporta agregar un UNIQUE constraint con ALTER TABLE, así que
esto reconstruye la tabla:
  1. Respalda el archivo de base de datos completo a backups/.
  2. Renombra game_analysis -> game_analysis_old.
  3. Crea la tabla nueva (con el constraint nuevo) vía create_all().
  4. Copia las filas de game_analysis_old, deduplicando por
     (game_pk, game_date, model_version) — se queda con la fila de mayor
     `id` (la más reciente) si hubiera duplicados bajo el esquema viejo.
  5. Borra game_analysis_old.

Solo aplica a SQLite (DATABASE_URL por defecto). Si usas PostgreSQL, agregar
el constraint es un `ALTER TABLE ... ADD CONSTRAINT` directo — Postgres sí
lo soporta sin reconstruir la tabla, y no lo necesita este script.

Uso:
    python db/migrate_v05.py
"""

import os
import shutil
from datetime import datetime

from sqlalchemy import inspect, text

from config import DATABASE_URL
from db.database import Base, engine, GameAnalysis


def _backup_sqlite_file():
    if not DATABASE_URL.startswith("sqlite"):
        return None
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        print(f"No existe {db_path} todavía -- nada que respaldar, se crea el esquema nuevo directo.")
        return None

    os.makedirs("backups", exist_ok=True)
    backup_path = "backups/mlb_edge_pre_v05.db"
    shutil.copy2(db_path, backup_path)
    print(f"Respaldo creado: {backup_path}")
    return db_path


def migrate():
    if not DATABASE_URL.startswith("sqlite"):
        print(f"DATABASE_URL no es SQLite ({DATABASE_URL}) -- esta migración es específica de "
              f"SQLite. En PostgreSQL, agrega el constraint con ALTER TABLE directamente.")
        return

    db_path = _backup_sqlite_file()

    inspector = inspect(engine)
    has_old_table = inspector.has_table("game_analysis")

    if not has_old_table:
        Base.metadata.create_all(engine)
        print("No había tabla game_analysis previa -- esquema v0.5 creado desde cero.")
        return

    existing_columns = {c["name"] for c in inspector.get_columns("game_analysis")}
    # ¿Ya tiene el constraint nuevo? create_all no lo detecta por columnas,
    # así que verificamos los índices únicos del esquema actual.
    unique_names = {uc["name"] for uc in inspector.get_unique_constraints("game_analysis")}
    if "uq_pred" in unique_names:
        print("El constraint uq_pred ya existe -- no hace falta migrar.")
        return

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE game_analysis RENAME TO game_analysis_old"))

    Base.metadata.create_all(engine)
    print("Tabla game_analysis nueva creada con el constraint uq_pred.")

    with engine.begin() as conn:
        old_columns = existing_columns
        new_columns = {c.name for c in GameAnalysis.__table__.columns}
        # Solo copiamos columnas que existen en ambos esquemas -- si el
        # esquema viejo no tenía model_version (versiones muy antiguas),
        # se completa con NULL y luego con MODEL_VERSION al deduplicar.
        common_columns = sorted(old_columns & new_columns - {"id"})

        rows = conn.execute(text(
            f"SELECT id, {', '.join(common_columns)} FROM game_analysis_old ORDER BY id DESC"
        )).mappings().all()

        seen_keys = set()
        inserted = 0
        for row in rows:
            model_version = row.get("model_version") or "unknown-pre-migracion"
            key = (row["game_pk"], row["game_date"], model_version)
            if key in seen_keys:
                continue  # ya insertamos la fila más reciente para esta clave
            seen_keys.add(key)

            values = {col: row[col] for col in common_columns}
            values["model_version"] = model_version
            placeholders = ", ".join(f":{c}" for c in values)
            conn.execute(
                text(f"INSERT INTO game_analysis ({', '.join(values)}) VALUES ({placeholders})"),
                values,
            )
            inserted += 1

        conn.execute(text("DROP TABLE game_analysis_old"))

    print(f"Migración completa: {inserted} fila(s) copiada(s) (deduplicadas por "
          f"game_pk+game_date+model_version) de {len(rows)} original(es).")


if __name__ == "__main__":
    migrate()
