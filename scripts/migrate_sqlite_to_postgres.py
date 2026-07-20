"""
Migra el histórico real de `mlb_edge.db` (SQLite) a un Postgres externo
(Neon/Supabase) -- versión de un solo paso de la guía manual en
`README.md` ("Migrar a Postgres gratuito"), pensada para correr dentro de
un workflow de GitHub Actions justo después de restaurar la caché de
`mlb_edge.db` que `daily_pipeline.yml` ya mantiene (no hay ningún
`mlb_edge.db` en este repo -- solo existe como archivo restaurado de esa
caché o como artifact de una corrida diaria).

Idempotente por tabla: si el destino YA tiene filas en una tabla, esa
tabla se salta con una advertencia (evita duplicar el histórico si este
script se corre dos veces por error) -- no borra ni sobreescribe nada.

Uso (con DATABASE_URL ya exportado apuntando al Postgres destino, y
mlb_edge.db ya presente en el directorio actual):
    python -m scripts.migrate_sqlite_to_postgres
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

import db.database as database


def _reset_autoincrement_sequence(conn, table) -> None:
    """Tras un INSERT masivo con `id` explícito (esta migración copia los
    ids reales de SQLite), la secuencia interna de Postgres para la PK
    autoincremental queda desincronizada -- sigue apuntando a 1, así que el
    primer INSERT real sin id explícito (ej. el próximo GameAnalysis/Pick
    que guarde el pipeline diario) choca con "duplicate key value violates
    unique constraint ...pkey" contra un id que la migración ya usó.
    Confirmado con la corrida real del 2026-07-20: los 14 juegos del día
    fallaron los 14 al persistir con exactamente ese error (Key (id)=(28)
    already exists). SQLite no tiene este problema (su AUTOINCREMENT
    interno ya conoce el máximo real tras el insert) -- por eso es un no-op
    si el destino no es Postgres, o si la tabla no tiene columna `id`
    autoincremental (ej. ActualResult, cuya PK es game_pk)."""
    if conn.dialect.name != "postgresql":
        return
    id_col = table.c.get("id")
    if id_col is None or not id_col.autoincrement:
        return
    conn.execute(text(
        f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
        f"COALESCE((SELECT MAX(id) FROM {table.name}), 1))"
    ))


def migrate(sqlite_path: str = "mlb_edge.db") -> dict[str, int]:
    sqlite_engine = create_engine(f"sqlite:///{sqlite_path}")
    SqliteSession = sessionmaker(bind=sqlite_engine)
    sqlite_session = SqliteSession()

    database.init_db()  # crea tablas/columnas que falten en el destino (Postgres)

    migrated: dict[str, int] = {}
    try:
        with database.engine.begin() as conn:
            for table in database.Base.metadata.sorted_tables:
                existing_count = conn.execute(select(func.count()).select_from(table)).scalar_one()
                if existing_count:
                    print(f"{table.name}: el destino ya tiene {existing_count} filas -- se salta (no se duplica).")
                    migrated[table.name] = 0
                    continue

                rows = [dict(r) for r in sqlite_session.execute(select(table)).mappings().all()]
                if not rows:
                    migrated[table.name] = 0
                    continue

                datetime_cols = [c.name for c in table.columns if isinstance(c.type, DateTime)]
                # Columnas NOT NULL con default escalar (ej. Pick.calibration_phase/
                # forced, default=False): SQLite nunca reforzo esa restriccion
                # retroactivamente sobre filas viejas (ni en ALTER TABLE ADD COLUMN
                # ni en insert crudo), asi que el dump trae NULL real en algunas --
                # Postgres si la exige. Se rellena con el mismo default que ya
                # usaria el ORM al insertar una fila nueva, nunca un valor inventado.
                not_null_scalar_defaults = {
                    c.name: c.default.arg for c in table.columns
                    if not c.nullable and c.default is not None and c.default.is_scalar
                }
                for row in rows:
                    for col in datetime_cols:
                        value = row.get(col)
                        if isinstance(value, str):
                            row[col] = datetime.fromisoformat(value)
                    for col_name, default_value in not_null_scalar_defaults.items():
                        if row.get(col_name) is None:
                            row[col_name] = default_value

                conn.execute(table.insert(), rows)
                _reset_autoincrement_sequence(conn, table)
                migrated[table.name] = len(rows)
                print(f"{table.name}: {len(rows)} filas migradas.")
    finally:
        sqlite_session.close()

    return migrated


if __name__ == "__main__":
    result = migrate()
    print("Migración completa:", result)
