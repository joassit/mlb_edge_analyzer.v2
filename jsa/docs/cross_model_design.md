# cross_model -- puente de resultados entre JSA, Game Flow y el modelo legado

## 1. Contexto y pregunta original

El usuario pidio "una base de datos de la cual podamos correr distintos
modelos [JSA, MLB legado, y el nuevo Game Flow]" y, al precisar el
alcance, especifico: poder **cruzar resultados con SQL directo** --
comparar predicciones/precision entre los 3 sistemas, no solo compartir
infraestructura de conexion.

Verificado contra el codigo real (agente de investigacion, 2026-07-19):
los 3 sistemas ya usan SQLAlchemy con URL de conexion configurable por
variable de entorno, y `game_pk` es `Integer` en los tres -- mismo tipo,
sin friccion para un join. Pero cada uno tiene su propio schema,
deliberadamente aislado:

- **Legado**: `DATABASE_URL` (`db/database.py`: `game_analysis`, `picks`,
  `actual_results`, `bets`, `feature_snapshots`) y `HISTORICAL_DATABASE_URL`
  (`historical_engine/db.py`: `historical_prediction`, ya con forma
  cercana a lo que se necesita: `game_pk, run_id, source, away_prob,
  home_prob, predicted_winner, actual_winner, correct`).
- **JSA**: `JSA_DATABASE_URL` (`jsa/storage/database.py`: `jsa_reports`,
  `results`) y `JSA_HISTORICAL_DATABASE_URL` (`jsa/historical/db.py`:
  `historical_report`, `historical_game`).
- **Game Flow**: vive dentro de `jsa/historical/`, usa la misma DB
  historica de JSA -- pero no persiste predicciones por juego, solo
  metricas LOSO agregadas (`game_flow_candidate_audit.py`).

No hace falta un proyecto nuevo: los 3 sistemas ya coexisten en este
repositorio. Lo que faltaba era una tabla que hablara el mismo idioma
entre los tres.

## 2. Decision: ETL de solo lectura, no instrumentar los pipelines

Dos formas de poblar una tabla compartida:
(a) modificar `persist_run()` (JSA) y `save_picks()`/`save_analysis()`
(legado) para que ademas escriban a la tabla compartida en el momento, o
(b) un sync/ETL separado que lee lo que cada sistema YA persiste.

Se eligio **(b)**, confirmado explicitamente por el usuario. Motivo:
cero riesgo sobre los pipelines de produccion de los 2 sistemas (ninguno
de los dos cambia una linea de su codigo de escritura), reversible
(borrar `cross_model/` no afecta a JSA ni al legado), y suficiente para
el objetivo real (analisis/comparacion, no serving en tiempo real).

## 3. Alcance de esta entrega: JSA + Game Flow + legado (via secret real)

Se construyo primero la parte verificable con datos reales dentro de
este sandbox: `sync_jsa.py` sincroniza dos fuentes, ambas dentro de
`jsa/historical/db.py` (ya ingerido, 5 temporadas reales):

- `sync_jsa_evidence_score()`: `evidence_score_raw` por juego (el score
  real de produccion de JSA), leido de `historical_report`.
- `sync_game_flow_candidates()`: el diff crudo de GF1/GF2 por juego
  (donde hay cobertura), leido de
  `game_flow_candidate_audit.load_records_with_game_flow_candidates()`.

El usuario autorizo explicitamente usar el secret ya configurado
`DATABASE_URL` (el mismo que usa `daily_pipeline.yml` para el modelo
legado en produccion) para tambien sincronizar picks reales del legado.
`sync_legacy.py` lee `db.database.Pick`/`ActualResult` -- filtrado a
mercado `moneyline` (el unico directamente comparable con predicciones
home/away de JSA/Game Flow) -- con su PROPIO engine/sesion construido a
partir de la URL recibida (nunca usa `db.database.SessionLocal`, que
queda atado al `DATABASE_URL` que existiera al importar el modulo por
primera vez; mismo patron que
`tests/test_historical_isolation.py::_seed_production_db()`). Probado
con una base sintetica (13 tests en `cross_model/tests/`, incluyendo uno
que hashea `picks`/`actual_results` antes y despues para confirmar cero
escritura); el sync contra la base de produccion real corre en
`.github/workflows/cross_model_sync.yml`, no directamente desde este
sandbox (sin acceso de red a esa base desde aqui).

`home_win_prob` para el legado SI se llena con un numero real (a
diferencia de JSA/Game Flow): `Pick.model_prob` es la probabilidad que
el legado ya usa para apostar dinero real -- se normaliza a "probabilidad
de que gane home" (`model_prob` si `selection=='home'`, `1-model_prob` si
`selection=='away'`).

## 4. Schema de `unified_model_predictions`

```
row_id, recorded_at, game_pk, game_date, season,
system            -- 'jsa' | 'game_flow' | 'mlb_legacy' (futuro)
model_name        -- 'evidence_score_raw' | 'gf1_starter_durability' | 'gf2_bullpen_dependency' | ...
model_version,
raw_score         -- valor crudo de la señal (nunca calibrado)
home_win_prob     -- SOLO si el sistema de origen produce una probabilidad genuinamente calibrada -- NULL hoy en los 3
predicted_winner  -- 'home'/'away', derivado del signo de raw_score
actual_winner     -- 'home'/'away', una vez que el juego termina
correct           -- predicted_winner == actual_winner, NULL mientras no se conozca actual_winner
source_ref        -- de que tabla/modulo de origen vino esta fila (trazabilidad)
UNIQUE(game_pk, system, model_name, model_version)
```

**Honestidad de diseño**: `home_win_prob` no se llena con un numero
inventado. Ningun sistema produce hoy una probabilidad calibrada
(`JSAReport.calibration.calibration_status` siempre `"uncalibrated"`) --
la columna existe para cuando eso cambie (ver ROADMAP.md, Fase 4). Hasta
entonces, la comparacion entre sistemas se hace sobre `predicted_winner`/
`correct` (accuracy), no sobre probabilidad/Brier -- esa es la limitacion
real, documentada, no oculta.

## 5. Ejemplo de la consulta que esto habilita

`cross_model/db.py::accuracy_by_system_and_model()` agrupa por
`(system, model_name, model_version)` y calcula accuracy en una sola
consulta SQL sobre una sola tabla -- sin tocar ninguna base de origen.
Con Postgres real y los 3 syncs corridos, la misma logica responde
directamente preguntas como "¿en que juegos JSA acerto y Game Flow no?"
via un self-join sobre `game_pk` filtrando por `system`.

## 6. Que falta para produccion real

- Confirmar que `cross_model_sync.yml` corrio contra el secret real sin
  error (dispara desde `main`, mismo requisito de `workflow_dispatch` que
  el resto de los workflows de JSA) y revisar el artifact de accuracy.
- Si se quiere de verdad "una sola instancia" para TODO (no solo el
  destino unificado), apuntar tambien `HISTORICAL_DATABASE_URL`/
  `JSA_DATABASE_URL` al mismo servidor Postgres -- decision de
  infraestructura, no de codigo (el sync ya funciona hoy sin eso, leyendo
  cada fuente por separado).
- Extender `sync_legacy.py` a `historical_engine.db.historical_prediction`
  si se decide tambien traer el backtest historico del legado (hoy solo
  se sincronizan los picks reales de produccion, `db.picks`).
- Si en el futuro se calibra `evidence_score_raw` (Fase 4 del ROADMAP de
  JSA) o el legado expone una probabilidad calibrada nueva, extender los
  syncs para llenar `home_win_prob` real en vez de dejarlo NULL.
- Un workflow de GitHub Actions (`jsa_cross_model_sync.yml`) que corra
  `sync_jsa.py` on-demand tras cada re-ingesta -- no construido todavia,
  se corre manualmente por ahora (`python -m cross_model.sync_jsa`).
