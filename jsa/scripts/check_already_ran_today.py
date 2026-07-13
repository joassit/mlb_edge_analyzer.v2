"""Guarda de idempotencia para el cron de respaldo -- Leccion 11 (ver plan):
GitHub `schedule` es best-effort, confirmado en `mlb_edge_analyzer.v2`
(solo 1 de 3 corridas programadas se disparo a tiempo en una semana real,
una se disparo 6h tarde). Con cron principal + cron de respaldo, si el
principal ya corrio, el respaldo no debe re-evaluar el mismo dia.

Compara juegos elegibles del dia (en Preview) contra reportes ya guardados
hoy -- no solo "existe alguna fila", para detectar tambien corridas
truncadas a medias. Imprime "true"/"false" a stdout, nada mas (se captura
en el workflow via `$GITHUB_OUTPUT`)."""

from __future__ import annotations

from datetime import date

from jsa import config
from jsa.data_sources import mlb_api
from jsa.registries import db as registries_db
from jsa.storage import database as storage_db


def already_ran_today() -> bool:
    today = date.today()
    games = mlb_api.get_schedule(today)
    if not games:
        # Sin juegos elegibles hoy (o la API fallo) -- no hay nada que
        # "ya corrio" en el sentido que le importa a esta guarda. Se deja
        # correr el pipeline igual (main.py maneja el caso de 0 juegos sin
        # error) en vez de asumir falsamente que ya se completo el dia.
        return False

    engine = registries_db.get_engine(config.DATABASE_URL)
    storage_db.init_storage(engine)
    saved = storage_db.manifests_for_date(engine, today)
    saved_game_ids = {row["game_id"] for row in saved}
    eligible_game_ids = {str(g["game_pk"]) for g in games}

    return eligible_game_ids.issubset(saved_game_ids) and len(saved_game_ids) > 0


if __name__ == "__main__":
    print("true" if already_ran_today() else "false")
