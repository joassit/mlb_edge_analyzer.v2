# cross_model

Puente de **solo lectura** entre sistemas de prediccion independientes de
este repositorio (JSA, Game Flow Engine, y a futuro el modelo MLB
legado). No es un cuarto modelo ni un reemplazo de ninguno de los
existentes -- es una tabla compartida (`unified_model_predictions`) que
permite comparar/cruzar resultados entre sistemas con SQL directo, sin
tocar ninguno de sus pipelines de produccion.

## Por que existe

JSA (`jsa/`), el modelo MLB legado (`db/`, `historical_engine/`) y el
Game Flow Engine (`jsa/historical/game_flow_candidate_audit.py`) viven en
el mismo repositorio pero mantienen bases de datos deliberadamente
aisladas entre si (`JSA_DATABASE_URL`/`JSA_HISTORICAL_DATABASE_URL` vs.
`DATABASE_URL`/`HISTORICAL_DATABASE_URL`) -- eso evita bugs de cruce de
datos, pero tambien hace imposible responder con una sola consulta SQL
preguntas como "¿en que juegos JSA acerto y el modelo legado no?".
`cross_model` resuelve eso sin romper el aislamiento: lee de las tablas
que cada sistema YA persiste, y escribe filas normalizadas a su propia
tabla -- en la MISMA instancia fisica de Postgres que los demas si
`UNIFIED_DATABASE_URL` apunta ahi, pero como un namespace de tabla
totalmente separado.

## Que existe hoy (alcance actual)

- `db.py`: schema de `unified_model_predictions`
  (`game_pk, game_date, season, system, model_name, model_version,
  raw_score, home_win_prob, predicted_winner, actual_winner, correct,
  source_ref`) + `upsert_prediction()` + `accuracy_by_system_and_model()`
  (el ejemplo concreto de "cruzar con SQL directo").
- `sync_jsa.py`: sincroniza `evidence_score_raw` (JSA historico) y GF1/GF2
  (Game Flow) desde `jsa/historical/db.py` -- CLI:
  `python -m cross_model.sync_jsa --jsa-historical-db ... --season ...`

## Que NO existe todavia

- Sync del modelo MLB legado (`db/database.py::picks`/`actual_results`,
  `historical_engine/db.py::historical_prediction`) -- su base de
  produccion real vive fuera del alcance de este sandbox; el diseño del
  sync (`sync_legacy.py`, mismo patron que `sync_jsa.py`) esta
  documentado en `jsa/docs/cross_model_design.md` pero no construido.
- Ninguna escritura automatica desde los pipelines en vivo -- este es un
  sync bajo demanda (ETL), no instrumentacion de `persist_run()` ni de
  `save_picks()`. Ver `jsa/docs/cross_model_design.md` Seccion 2 para la
  justificacion de esa eleccion.
- `home_win_prob` calibrado: ningun sistema produce hoy una probabilidad
  real calibrada (JSA: `calibration_status` siempre `"uncalibrated"`) --
  la columna existe para cuando eso cambie, nunca se llena con un numero
  inventado mientras tanto.

Ver `jsa/docs/cross_model_design.md` para el diseño completo.
