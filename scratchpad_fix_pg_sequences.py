"""
Reparación de un solo uso: la migración del 2026-07-19 (scripts/
migrate_sqlite_to_postgres.py, ANTES del fix de secuencias en main
commit 1927b8c) copió los ids reales de SQLite via INSERT explícito --
Postgres nunca avanzó su secuencia interna al hacer esto, así que sigue
en 1 y el primer INSERT real sin id explícito (el pipeline diario de
hoy) choca con "duplicate key value violates unique constraint
...pkey" contra un id que la migración ya usó (confirmado: corrida
29768932113, Key (id)=(28) already exists en game_analysis).

Este script corre setval() contra el Postgres real (secrets.DATABASE_URL)
para las 4 tablas con id autoincremental -- una sola vez, de solo
escritura sobre la secuencia (nunca toca filas). Después de esto, el
código nuevo en main (commit 1927b8c) ya se encarga de que esto no
vuelva a pasar en futuras migraciones.
"""
import sys
sys.path.insert(0, ".")

from sqlalchemy import text
from db.database import engine, Base

with engine.begin() as conn:
    for table in Base.metadata.sorted_tables:
        id_col = table.c.get("id")
        if id_col is None or not id_col.autoincrement:
            print(f"{table.name}: sin columna id autoincremental -- se salta.")
            continue
        before = conn.execute(text(f"SELECT MAX(id) FROM {table.name}")).scalar()
        conn.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table.name}), 1))"
        ))
        print(f"{table.name}: secuencia resincronizada (MAX(id)={before}).")

print("FIX_SEQUENCES_OK")
