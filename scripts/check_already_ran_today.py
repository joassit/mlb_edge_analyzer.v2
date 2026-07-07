"""
Guarda de idempotencia para el cron de respaldo de daily_pipeline.yml.

GitHub Actions documenta que el disparador `schedule` es best-effort:
puede retrasarse o saltarse por completo durante picos de carga en su
infraestructura, sin dejar ningún rastro de error. Confirmado con
evidencia real en este proyecto: de las 3 corridas programadas que
debieron dispararse en una semana (07-05, 07-06 y 07-07, todas a las
15:00 UTC), solo 1 realmente se disparó -- y lo hizo a las
2026-07-06T21:12:26Z, más de 6 horas tarde.

Por eso el workflow agrega un segundo cron de respaldo ~2h después del
principal. Si el principal SÍ se disparó a tiempo, el respaldo no debe
volver a analizar el mismo día: gastaría presupuesto de Odds API
(~500 requests/mes) en llamadas redundantes y subiría un segundo
reporte del mismo día. Este script responde una sola pregunta: ¿ya
existe una predicción de hoy en game_analysis?

Uso (ver .github/workflows/daily_pipeline.yml):
    python -m scripts.check_already_ran_today
(no "python scripts/check_already_ran_today.py" -- ejecutar el archivo
por ruta directa pone scripts/ en sys.path en vez de la raíz del repo,
y `import db.database` falla con ModuleNotFoundError; -m sí antepone
el directorio actual). Imprime "true" o "false" a stdout -- nada más.
"""

from datetime import date

import db.database as database


def already_ran_today() -> bool:
    """True si ya existe al menos una fila de game_analysis con
    game_date = hoy (UTC) -- mismo campo que el proyecto ya usa para su
    propio dedup de upsert (game_pk, game_date, model_version), ver
    save_analysis() en db/database.py."""
    database.init_db()  # no-op si las tablas ya existen -- cubre caché fría/primer uso
    session = database.SessionLocal()
    try:
        today = date.today().strftime("%Y-%m-%d")
        return session.query(database.GameAnalysis).filter_by(game_date=today).count() > 0
    finally:
        session.close()


if __name__ == "__main__":
    print("true" if already_ran_today() else "false")
