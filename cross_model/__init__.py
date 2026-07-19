"""`cross_model` -- puente de solo lectura entre sistemas de prediccion
independientes (JSA, Game Flow Engine, y a futuro el modelo MLB legado).

Nunca escribe a las tablas de produccion/historicas de ningun sistema --
solo LEE de ellas (`jsa/historical/db.py`, y a futuro `db/database.py`/
`historical_engine/db.py` del proyecto legado) y escribe a su propia
tabla (`unified_model_predictions`), pensada para vivir en la MISMA
instancia fisica de Postgres que los demas sistemas si `UNIFIED_DATABASE_URL`
apunta ahi -- pero como namespace de tabla completamente separado, mismo
principio de aislamiento que ya usan JSA y el proyecto legado entre si.

Ver `jsa/docs/cross_model_design.md` para el diseno completo."""
