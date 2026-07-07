"""
Migración v0.8: agrega away_negbin_prob / home_negbin_prob a game_analysis
(modelo de Binomial Negativo, ver model/negbin_model.py, en paralelo al
Skellam existente).

Son columnas nullable nuevas -- el mismo caso que ya cubre
_auto_add_missing_columns() en db/database.py (que corre automáticamente
en cada init_db()), así que técnicamente este script es redundante en
cuanto alguien llame init_db() de nuevo. Se agrega igual, siguiendo el
patrón explícito de migrate_v07.py, para poder aplicar el cambio de
esquema sin tener que correr el pipeline completo, y para que quede
documentado como un cambio de versión con nombre propio.

A diferencia de migrate_v05.py, esto NO requiere reconstruir la tabla:
ALTER TABLE ADD COLUMN es una operación aditiva que SQLite y Postgres
soportan directo, sin tocar los datos existentes.

Uso:
    python db/migrate_v08.py
"""

from sqlalchemy import inspect, text

from db.database import engine, GameAnalysis

_NEW_COLUMNS = ["away_negbin_prob", "home_negbin_prob"]


def migrate():
    inspector = inspect(engine)
    if not inspector.has_table(GameAnalysis.__tablename__):
        print("No existe game_analysis todavía -- las columnas se crean junto con la tabla al llamar init_db().")
        return

    existing_columns = {c["name"] for c in inspector.get_columns(GameAnalysis.__tablename__)}
    missing = [name for name in _NEW_COLUMNS if name not in existing_columns]
    if not missing:
        print("away_negbin_prob / home_negbin_prob ya existen -- no hace falta migrar.")
        return

    columns_by_name = {c.name: c for c in GameAnalysis.__table__.columns}
    with engine.begin() as conn:
        for name in missing:
            col_type = columns_by_name[name].type.compile(engine.dialect)
            conn.execute(text(f"ALTER TABLE game_analysis ADD COLUMN {name} {col_type}"))
    print(f"Columna(s) agregada(s) a game_analysis: {', '.join(missing)}.")


if __name__ == "__main__":
    migrate()
