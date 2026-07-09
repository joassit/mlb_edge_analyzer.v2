"""
historical_engine — motor de backtesting histórico, entrenamiento y
validación estadística, TOTALMENTE AISLADO del sistema de producción.

Garantías de aislamiento (ver tests/test_historical_isolation.py):
  - Base de datos propia (historical_engine/db.py), archivo físico distinto
    de mlb_edge.db, con su propio SQLAlchemy Base/engine/SessionLocal. No
    comparte ninguna tabla, ni ningún objeto de sesión, con db/database.py.
  - Nunca importa db.database.SessionLocal ni escribe en GameAnalysis,
    ActualResult, Pick, Bet o FeatureSnapshot.
  - Las únicas funciones de producción que reutiliza son de LECTURA pura
    contra APIs externas (data/mlb_api.py::get_schedule/get_game_result),
    sin estado ni caché compartido -- no hay ruta posible de contaminación.
  - Las estadísticas point-in-time NO reutilizan data/stats.py (esas
    funciones cachean por temporada completa, no por fecha de corte --
    reusarlas sería la fuga de información que este módulo existe para
    evitar). Ver historical_engine/point_in_time_provider.py.

Este paquete es research/backtesting -- nunca se importa desde main.py,
reports/generate_report.py, ni ningún path del pipeline diario de
producción.
"""
