"""Utilidad de persistencia compartida entre TODOS los motores de storage
de JSA (`storage/database.py` produccion, `historical/db.py`,
`registries/db.py`) -- un solo lugar donde vive la logica dialect-aware,
para que el gap de Postgres que tenia `persist_run()` (`.prefix_with("OR
IGNORE", dialect="sqlite")`, que en Postgres no hacia nada) no pueda
reintroducirse por separado en cada motor."""

from __future__ import annotations

from sqlalchemy import Table
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Connection


def insert_ignore_duplicates(conn: Connection, table: Table, **values) -> None:
    """Insert-o-saltar-si-ya-existe, portable entre SQLite y Postgres --
    los dos dialectos que JSA declara soportar (ver `jsa/README.md`).
    Para cualquier otro dialecto se cae a un INSERT normal; el caller debe
    seguir envolviendo la llamada en un `except IntegrityError` propio si
    ese dialecto no soporta `ON CONFLICT DO NOTHING`."""
    dialect_name = conn.engine.dialect.name
    if dialect_name == "postgresql":
        stmt = postgresql.insert(table).values(**values).on_conflict_do_nothing()
    elif dialect_name == "sqlite":
        stmt = sqlite.insert(table).values(**values).on_conflict_do_nothing()
    else:
        stmt = table.insert().values(**values)
    conn.execute(stmt)
