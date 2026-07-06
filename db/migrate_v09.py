"""
Migración v0.9: agrega prob_source / directional_discrepancy a `picks`
(trazabilidad de qué modelo generó cada pick de moneyline, ver
config.PICK_PROBABILITY_SOURCE y model/picks.py).

Son columnas nullable nuevas -- el mismo caso que ya cubre
_auto_add_missing_columns() en db/database.py (corre automáticamente en
cada init_db()), así que este script es redundante en cuanto alguien llame
init_db() de nuevo. Se agrega igual, siguiendo el patrón explícito de
migrate_v07.py/migrate_v08.py, para poder aplicar el cambio de esquema sin
correr el pipeline completo.

A diferencia de migrate_v05.py, esto NO requiere reconstruir la tabla:
ALTER TABLE ADD COLUMN es aditivo, sin tocar los datos existentes.

Uso:
    python db/migrate_v09.py
"""

from sqlalchemy import inspect, text

from db.database import engine, Pick

_NEW_COLUMNS = ["prob_source", "directional_discrepancy"]


def migrate():
    inspector = inspect(engine)
    if not inspector.has_table(Pick.__tablename__):
        print("No existe picks todavía -- las columnas se crean junto con la tabla al llamar init_db().")
        return

    existing_columns = {c["name"] for c in inspector.get_columns(Pick.__tablename__)}
    missing = [name for name in _NEW_COLUMNS if name not in existing_columns]
    if not missing:
        print("prob_source / directional_discrepancy ya existen -- no hace falta migrar.")
        return

    columns_by_name = {c.name: c for c in Pick.__table__.columns}
    with engine.begin() as conn:
        for name in missing:
            col_type = columns_by_name[name].type.compile(engine.dialect)
            conn.execute(text(f"ALTER TABLE picks ADD COLUMN {name} {col_type}"))
    print(f"Columna(s) agregada(s) a picks: {', '.join(missing)}.")


if __name__ == "__main__":
    migrate()
