"""
Migración v0.7: agrega el índice ix_game_date_pk (game_date, game_pk) a
game_analysis.

El único índice único que existía hasta ahora (uq_pred, sobre game_pk +
game_date + model_version) tiene game_pk como columna izquierda, no
game_date -- no acelera queries que filtran SOLO por game_date, como
tracking/results_tracker.compute_metrics() o
db.database.get_predictions_without_result(), que sin esto hacen full
table scan tanto en SQLite como en Postgres.

A diferencia de migrate_v05.py, esto NO requiere reconstruir la tabla:
CREATE INDEX es una operación aditiva que SQLite y Postgres soportan
directo, sin tocar los datos existentes.

Uso:
    python db/migrate_v07.py
"""

from sqlalchemy import inspect, text

from db.database import engine, GameAnalysis


def migrate():
    inspector = inspect(engine)
    if not inspector.has_table(GameAnalysis.__tablename__):
        print("No existe game_analysis todavía -- el índice se crea junto con la tabla al llamar init_db().")
        return

    existing_indexes = {ix["name"] for ix in inspector.get_indexes(GameAnalysis.__tablename__)}
    if "ix_game_date_pk" in existing_indexes:
        print("El índice ix_game_date_pk ya existe -- no hace falta migrar.")
        return

    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX ix_game_date_pk ON game_analysis (game_date, game_pk)"
        ))
    print("Índice ix_game_date_pk creado sobre game_analysis (game_date, game_pk).")


if __name__ == "__main__":
    migrate()
