"""
Script temporal de solo-lectura para diagnosticar el secret DATABASE_URL
que aparecio entre la corrida del 2026-07-19 20:12 UTC (HAS_EXTERNAL_DB=false,
uso de cache SQLite) y la corrida manual del 2026-07-20 17:42 UTC
(HAS_EXTERNAL_DB=true, "Run pipeline" escribio 14 juegos sin errores pero
"Validate run" fallo porque mlb_edge.db no existe localmente -- seria el
comportamiento esperado si ahora se escribe a una DB externa real).

Nunca imprime la URL cruda (podria contener credenciales) -- solo el
dialecto/driver, los nombres de tabla existentes, y conteos/fechas.
"""
import sys
sys.path.insert(0, ".")

from sqlalchemy import inspect, func
from config import DATABASE_URL
from db.database import SessionLocal, engine, GameAnalysis, ActualResult, Pick, Bet, FeatureSnapshot

print(f"dialect: {engine.dialect.name}")
print(f"driver: {engine.dialect.driver}")
print(f"url_scheme_only: {DATABASE_URL.split(':')[0]}")

insp = inspect(engine)
tables = sorted(insp.get_table_names())
print(f"tablas existentes: {tables}")

session = SessionLocal()
try:
    for name, model in [
        ("game_analysis", GameAnalysis), ("actual_results", ActualResult),
        ("picks", Pick), ("bets", Bet), ("feature_snapshots", FeatureSnapshot),
    ]:
        n = session.query(func.count(model.id)).scalar()
        dates = session.query(model.game_date).distinct().order_by(model.game_date).all() if hasattr(model, "game_date") else []
        date_list = sorted({d[0] for d in dates})
        print(f"{name}: n={n} fechas_distintas={date_list}")
finally:
    session.close()

print("DIAGNOSTICO_OK")
