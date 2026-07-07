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
reporte del mismo día.

Primera versión de este script solo miraba "¿existe AL MENOS UNA fila de
hoy?" -- eso deja pasar como "completo" una corrida que truena a medias:
run_pipeline() (main.py) guarda cada fila de resultados en un loop SIN
try/except por-ítem (a diferencia de analyze_today(), que sí aísla
errores por juego), así que un crash ahí (IntegrityError agotando su
único reintento, disco lleno, un OOM/timeout del runner) deja algunas
filas guardadas y el resto del día sin analizar -- "algo existe" no
significa "todo se guardó".

Por eso el criterio real es: filas de game_analysis de hoy >= juegos
ELEGIBLES de hoy (mismo filtro Preview + abridores confirmados que usa
analyze_today() ANTES de tocar ninguna stats API, ver main.py). No
replica el tercer motivo de descarte de main.py (falta ERA/OPS/bullpen,
solo se sabe DURANTE el análisis) -- que el respaldo reintente un juego
descartado por eso es deseable, no un bug. Si no hay ningún juego
elegible hoy (día libre, todo pospuesto/TBD), 0 >= 0 -- se considera
completo, no tiene sentido reintentar un día sin nada que analizar.

Uso (ver .github/workflows/daily_pipeline.yml):
    python -m scripts.check_already_ran_today
(no "python scripts/check_already_ran_today.py" -- ejecutar el archivo
por ruta directa pone scripts/ en sys.path en vez de la raíz del repo,
y `import db.database` falla con ModuleNotFoundError; -m sí antepone
el directorio actual). Imprime "true" o "false" a stdout -- nada más.
"""

from datetime import date

import db.database as database
from data.mlb_api import get_schedule


def _count_eligible_games_today() -> int:
    """Juegos de hoy que analyze_today() SÍ intentaría analizar -- mismo
    criterio de descarte que main.py aplica ANTES de tocar ninguna stats
    API (Preview + ambos abridores confirmados, ver main.py::analyze_today,
    líneas del descarte por abstract_state y por TBD)."""
    games = get_schedule(date.today())
    return sum(
        1 for g in games
        if g.get("abstract_state") == "Preview" and g.get("away_pitcher_id") and g.get("home_pitcher_id")
    )


def already_ran_today() -> bool:
    """True si la corrida de hoy ya está completa: filas de
    game_analysis con game_date = hoy (mismo campo que el proyecto ya
    usa para su propio dedup de upsert, ver save_analysis() en
    db/database.py) >= juegos elegibles de hoy."""
    database.init_db()  # no-op si las tablas ya existen -- cubre caché fría/primer uso
    session = database.SessionLocal()
    try:
        today = date.today().strftime("%Y-%m-%d")
        actual = session.query(database.GameAnalysis).filter_by(game_date=today).count()
    finally:
        session.close()

    eligible = _count_eligible_games_today()
    return actual >= eligible


if __name__ == "__main__":
    print("true" if already_ran_today() else "false")
