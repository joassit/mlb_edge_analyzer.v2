# Roadmap de JSA v3.0

Este documento existe para que nunca sea ambiguo que esta construido con
calidad real y que queda pendiente. Sigue las 7 fases de la Seccion 19 de
la especificacion maestra ("JSA v3.0 — Especificación Maestra Unificada").

## Construido en esta entrega (Fases 1-2 + capa de gobernanza desde el dia 1)

- Modelos de datos Pydantic v2 exactos (Seccion 3), `snapshot_hash` desde
  el primer commit, migracion aditiva `3.0 -> 3.1` real (campos de
  contexto de liga).
- Context Detector (Seccion 5), Rule Engine + Weight Engine + Weight Audit
  (Seccion 6) con conmutatividad probada, Rule Trace (6.6).
- Los 7 pilares (Seccion 7.1) + Feature Contribution/Dominance Detector
  (7.2) + mecanismo de Pilar Experimental probado (7.3) + versionado por
  pilar (7.4).
- Evidence Engine completo: Evidence Score, CRI, Uncertainty Index,
  Auditoria Matematica programatica (Seccion 8.1-8.3, 8.5).
- Modulo de Carreras Proyectadas y Handicap + Consistency Flag (Seccion 9).
- Confidence Gate con los 7 criterios (Seccion 10.2) -- honesto: nunca
  pasa mientras el modelo no este calibrado.
- Los 4 registries de extensibilidad (Feature, Rule, Pillar, Market,
  Schema Migration) como tablas append-only reales, no placeholders
  (Secciones 3.3, 4.3, 6.2, 7.3, 10.5bis).
- Manifest de ejecucion + las 12 reglas de invalidacion automatica activas
  desde el primer commit (Secciones 14.1, 15).
- Provenance Graph append-only con advertencias de propagacion (14.4).
- JSAReport v3 completo (Seccion 11.8): hashes, manifest, weight audit,
  rule trace, feature contribution, calibracion, confidence gate,
  reconstruction token.
- GitHub Actions: `jsa_tests.yml` + `jsa_daily_pipeline.yml`, con las
  lecciones operativas de `mlb_edge_analyzer.v2` aplicadas desde el dia 1
  (ver README).

## Construido en la segunda entrega (motor historico + Monte Carlo + legado)

JSA paso a ser el proyecto principal, con autonomia tecnica completa
frente a `mlb_edge_analyzer.v2` (cero dependencias de codigo, solo
patrones de diseño reusados por su merito). Se agrego:

- `jsa/historical/`: motor de ingesta historica propio (temporadas
  2022-2026, mismo rango que `historical_engine` del proyecto hermano),
  con proveedor de estadisticas punto-en-el-tiempo (`stats=byDateRange`,
  climatologia en vez de clima real, roster con fecha de corte -- nunca
  `stats=season`). Reconstruye un `GameSnapshot` por juego y lo evalua con
  **la misma funcion pura de produccion** (`engine.orchestrator.
  evaluate_game()`, sin duplicar logica) -- confirmacion practica de que
  el diseño "una unica funcion de evaluacion, en vivo y en backtest" si
  funciona. Resumible (una temporada cortada a medias no re-procesa lo ya
  hecho), aislado en su propia base de datos (Seccion 4.2).
- `jsa/historical/validation.py`: Brier, LogLoss, ECE, MCE, Home Bias
  Audit (13.3) sobre resultados reales, mas benchmarking obligatorio
  (12.3) contra baselines ingenuos (constante, siempre-local, mejor-OPS,
  mejor-ERA-abridor) y los 3 modelos legado.
- `jsa/historical/monte_carlo.py`: Monte Carlo Audit (13.7bis) real --
  N simulaciones perturbando `PillarWeights`, Critical Failure Factor
  (correlacion peso-vs-Brier por pilar), Feature Stability, Weight
  Stability, Probability Collapse (sobre una pseudo-probabilidad proxy,
  nunca expuesta como calibracion real).
- `jsa/legacy/`: heuristico ERA/OPS, Skellam, NegBin + las constantes ya
  calibradas de `mlb_edge_analyzer.v2` (`NEGBIN_DISPERSION=3.0`,
  `SKELLAM_SHRINKAGE_ALPHA=0.5`), preservadas como rama secundaria de
  benchmarking -- nunca el motor primario (`tests/test_production_isolation.py`
  lo hace cumplir).
- `.github/workflows/jsa_historical_ingest.yml`: ingesta por temporada via
  `workflow_dispatch`, timeout 340 min (misma leccion de
  `historical_ingest.yml` del proyecto hermano).
- Postgres desde el dia 1, para real: se encontro y corrigio un gap real
  (`storage/database.py::persist_run()` usaba
  `.prefix_with("OR IGNORE", dialect="sqlite")`, que en Postgres no hacia
  nada) -- ahora `storage/dialect_utils.py::insert_ignore_duplicates()` es
  dialect-aware (SQLite y Postgres), compartido por los 3 motores de
  storage (`registries`, `storage`, `historical`), y **verificado contra
  un Postgres real** en esta sesion (`tests/test_postgres_compat.py`, skip
  automatico si `TEST_POSTGRES_URL` no esta configurado).

**Pendiente, bloqueado por falta de acceso a datos (no por falta de
codigo):** migrar los ~100 picks historicos reales que ya genera el
pipeline diario de `mlb_edge_analyzer.v2` -- viven en su `mlb_edge.db`/
Postgres, fuera del alcance de este sandbox. Cuando ese acceso exista, el
mapeo es directo: cada `Pick` ya tiene `game_pk`/`game_date`/`market`/
`model_prob`/`result` -- se puede alinear 1:1 contra un
`GameSnapshot` reconstruido por `jsa/historical/` para el mismo `game_pk`
y usarse en `validation.py` como un dataset adicional (picks reales, no
solo resultados). No se sintetiza ni se aproxima este dataset mientras
tanto.

## Construido en la tercera entrega (unificacion historica, Postgres real, contribucion de pilares)

Las 5 temporadas 2022-2026 quedaron ingeridas dos veces: primero contra
SQLite efimero (una base separada por corrida de `jsa_historical_ingest.yml`,
sin `JSA_HISTORICAL_DATABASE_URL` configurado todavia), y luego contra un
Postgres real ya configurado (mismo workflow, mismo codigo, sin cambios --
la promesa de "Postgres desde el dia 1" de la segunda entrega se confirmo
en la practica). Se agrego:

- `jsa/historical/merge.py` + `cli.py merge`: fusiona N bases historicas
  separadas (una por temporada) en una sola, idempotente
  (`insert_ignore_duplicates`) -- necesario porque `validation.py`/
  `monte_carlo.py` comparan temporadas desde UNA base, y sin
  `JSA_HISTORICAL_DATABASE_URL` cada temporada vivia aislada en su propio
  artifact de GitHub Actions.
- `.github/workflows/jsa_historical_validate.yml`: descarga N artifacts de
  temporada, los fusiona, corre `validate` sobre la base combinada -- vive
  en Actions (no en un sandbox local) porque el egress a Azure Blob
  Storage (donde GitHub aloja los artifacts) puede estar bloqueado fuera
  de un runner de Actions.
- `psycopg2-binary` paso de opcional a dependencia real de
  `requirements.txt` -- un runner de GitHub Actions no puede instalarlo
  ad hoc a mitad de un workflow como si fuera un shell de desarrollo
  local; sin esto, cualquier workflow apuntado a Postgres fallaba en el
  primer connect.
- `jsa/analytics/pillar_contribution.py` (`PillarContributionAnalyzer`):
  agrega, sobre N juegos, la contribucion por pilar que
  `evidence_engine.compute_feature_contribution()` YA calcula por juego
  individual (Seccion 7.2) y que ya vive en todo `JSAReport.
  feature_contribution` -- vectorizado con numpy, deliberadamente puro y
  ubicado junto a `engine/`/`domain/`/`storage/` (no junto al paquete
  historico) para poder importarse desde produccion el dia que haga falta
  sin violar el aislamiento que `tests/test_production_isolation.py` hace
  cumplir. Reporta, por pilar: media/mediana/desvio/p10/p90 de
  contribucion porcentual, tasa de `dominance_warning`, `top_contributor_rate`
  (tasa de ser el pilar con mayor contribucion del juego), tasa de
  `advantage==0` (pilar "mudo") y tasa de contribucion despreciable. El
  lado con I/O que lee `historical_report`
  vive en `jsa/historical/pillar_contribution.py` (`cli.py
  pillar-contribution`, y ya integrado como paso extra de `cli.py
  validate`).

## Pilar Context desmudado (primer arreglo tras el diagnostico de PillarContributionAnalyzer)

`PillarContributionAnalyzer` mostro `team_quality`/`context`/`trend`/`historical`
con `zero_advantage_rate=100%` en las 5 temporadas. Diagnostico: `trend`/
`historical` son stubs intencionales (quedan asi); `context` solo se
movia con `extreme_travel`, que dependia de `travel_distance` -- un campo
que ni `snapshot_reconstruction.py` (historico) ni `snapshot_builder.py`
(produccion) poblaban nunca. Se corrigio en ambos lados (mismo principio
de "una unica logica de evaluacion, en vivo y en backtest" ya aplicado en
`engine/orchestrator.py`):

- `data_sources/park_factors.py::distance_miles()`: haversine pura entre
  los estadios de dos equipos, usando las coordenadas ya tabuladas.
- `historical/ingestion.py::build_previous_park_index()`: para cada
  (team_id, game_pk), el estadio donde ese equipo jugo su partido
  inmediato anterior -- calculado en memoria a partir del schedule que
  `fetch_season_games()` YA trae completo por temporada, **cero llamadas
  de red adicionales** para el backfill historico.
- `data_sources/mlb_api.py::get_previous_game_location()` +
  `data_sources/travel.py::preload_travel_distances()`: equivalente para
  produccion en vivo (que no tiene el schedule completo precargado) --
  mismo patron de precarga por lote que `weather.py::preload_weather()`.
- `historical/point_in_time_provider.py::historical_weather()` ahora
  tambien pide `windspeed_10m` a Open-Meteo (ya estaba en la respuesta,
  solo faltaba pedirla) -- `weather_wind_speed` ya funcionaba en
  produccion (`data_sources/weather.py`), pero nunca en el lado historico.

**Pendiente antes de recalibrar** (ver conversacion): la revision de la
asimetria de shrinkage `starter` vs `bullpen` (`SHRINKAGE_K_IP=60` sin
equivalente en `bullpen.py`, que ademas tiene el peso base mas alto de los
7 pilares) -- explicitamente diferida hasta despues de ver los resultados
con `team_quality` ya activo (ver seccion siguiente).

## Pilar `team_quality` desmudado: lesiones (IL) + `closer_available`

Segundo arreglo del diagnostico de `PillarContributionAnalyzer`. Fuente:
`statsapi.mlb.com/api/v1/transactions` (validada por un spike de
investigacion real, cobertura confirmada en las 5 temporadas 2022-2026),
elegida en vez de scraping de Pro Sports Transactions -- fuente oficial,
reusa la infraestructura HTTP ya existente, cero riesgo de fragilidad de
scraping.

**Criterio de "lesion clave"** (Seccion team_quality), acordado
explicitamente antes de implementar: bateador con >=50 PA o pitcher con
>=15 IP en los 30 dias previos a su colocacion en la lista de lesionados
(point-in-time, no acumulado de toda la temporada) -- punto de partida sin
calibrar todavia, mismo espiritu que `SMALL_SAMPLE_OFFENSE_PA` en
`config.py`. Los cerradores/relevistas de alto apalancamiento no cruzan
este umbral (IP bajo por diseño de su rol) -- su disponibilidad se mide
por separado via `closer_available`, sin filtro de PA/IP: un cerrador
lesionado importa sin importar cuantas entradas acumulo.

Mismo principio de "una unica logica, en vivo y en backtest, nunca
divergiendo" que Context -- implementado en ambos lados, DUPLICADO a
proposito (nunca importado entre `historical`/produccion, ver
`tests/test_production_isolation.py`):

- `historical/injuries.py` / `data_sources/injuries.py`: `fetch_season_transactions()`
  trae TODA la temporada de transacciones en una sola llamada de red (mismo
  patron que `fetch_season_games()`/`build_previous_park_index()`);
  `parse_il_events()` (puro, clasifica "placed"/"activated" via regex
  sobre `description`, ignora "transferred"); `build_injury_index()` es la
  UNICA funcion que pega la red mas alla del fetch inicial -- una vez POR
  JUGADOR con al menos un evento "placed" en la temporada (nunca por
  juego), para evaluar su PA/IP reciente contra el umbral. `is_injured_as_of()`/
  `key_injuries_as_of()` son puras sobre el indice ya construido.
- `historical/point_in_time_provider.py::bullpen_era_as_of()` /
  `data_sources/stats.py::get_bullpen_era()`: ahora devuelven
  `{"era", "closer_pitcher_id"}` en vez de solo el ERA -- el cerrador
  (relevista con mas saves point-in-time del roster) se identifica DENTRO
  del mismo loop que ya calculaba el ERA de bullpen, aprovechando que
  `saves` viene en el mismo payload que `era`/`inningsPitched` -- **cero
  llamadas de red adicionales** para detectar al cerrador.
- `historical/snapshot_reconstruction.py` / `data_sources/snapshot_builder.py`:
  cruzan `closer_pitcher_id` contra el `InjuryIndex` para poblar
  `home/away_closer_available`, y consultan `key_injuries_as_of()` para
  `home/away_key_injuries`.
- `main.py`: `injuries.build_today_injury_index()` se llama UNA vez por
  corrida diaria (igual que `build_league_context()`/`preload_weather()`/
  `preload_travel_distances()`), nunca por juego.

**Costo de red real**: 1 llamada por temporada (transacciones) + 1
llamada por jugador colocado en IL en toda la temporada (evaluacion de
PA/IP reciente) -- nunca escala con el numero de juegos.

**Limitacion aceptada**: un jugador traspasado de equipo la MISMA
temporada en la que tambien se lesiono queda indexado bajo su equipo mas
reciente para TODOS sus eventos de esa temporada (no el equipo real al
momento de cada evento) -- caso de baja probabilidad, no justifica
trackear equipo por evento todavia.

**Bundle de re-ingesta**: Context + `team_quality` (lesiones +
`closer_available`) quedan listos para una unica re-ingesta optimizada de
las 5 temporadas contra Postgres -- disparada solo con confirmacion
explicita, no automaticamente al mergear este cambio (ver "Regla dura"
al final de este documento). La revision de shrinkage `starter`/`bullpen`
queda para despues de ver esos resultados.

## `starter_projected_ip` desmudado (tercer campo del mismo bundle)

Revision final de campos "de valor y bajo/medio esfuerzo" antes de la
re-ingesta combinada: `home/away_starter_projected_ip` (IP proyectada por
salida) tenia fuente real en produccion
(`data_sources/stats.py::get_pitcher_command()`, `ip / games started`)
pero siempre quedaba en `None` sobre datos historicos -- misma asimetria
que ya se corrigio con `travel_distance`/`weather_wind_speed`.

Impacto real (no solo CRI): `projected_ip` alimenta `long_outing`/
`short_outing_bullpen_game` en el Context Detector (Seccion 5), que a su
vez disparan reglas del Rule Engine (Seccion 6.3) que mueven peso real
entre `starter` y `bullpen` (`±0.06`/`±0.10`) -- exactamente el tipo de
campo que el criterio acordado pedia ("impacto real en los pilares
actuales"), a diferencia de `bullpen_ip_last_3_days` (unicamente
Uncertainty Index, ya descartado).

- `historical/point_in_time_provider.py::pitcher_era_ip_as_of()`: cambia
  de tupla `(era, ip)` a `{"era", "ip", "projected_ip"}` -- `projected_ip`
  se calcula del MISMO `stat` dict que ya se pedia para ERA/IP
  (`gamesStarted` viene en el mismo payload), **cero llamadas de red
  adicionales**.
- `historical/snapshot_reconstruction.py`: puebla `home/away_starter_projected_ip`
  desde ese mismo dict.

Con este campo, el Context Detector queda completamente desmudado en el
lado historico salvo `bullpen_fatigue` (excluido a proposito, ver seccion
de Context arriba) -- revisado explicitamente campo por campo contra
`engine/pillars/*.py` y `engine/context_detector.py`: ningun otro campo
consumido por un pilar o por una regla del Rule Engine queda sin fuente
real de este lado.

## Explicitamente NO construido todavia (y por que)

Estas piezas requieren mas historial de produccion real acumulado (varias
temporadas YA ingeridas via `jsa/historical/`, no solo el mecanismo para
ingerirlas) para tener sentido -- construirlas ahora seria fingir una
validacion que el propio spec prohibe declarar sin evidencia (Seccion
10.4: n>=50 juegos/temporada, walk-forward de >=3 temporadas; Seccion
13.6: ventanas moviles mensuales).

### Fase 3 — Significancia estadistica formal (Seccion 12.8)
- Bootstrap, McNemar, permutation test sobre los resultados de
  `historical/validation.py` -- el benchmarking numerico YA existe (ver
  arriba), lo que falta es la prueba formal de que una diferencia de
  Brier no es ruido de muestra chica antes de graduar nada de
  `experimental` a `active` (ver `engine/rule_engine.py`).
- Experiment Engine completo con `experiment_registry` poblado (la tabla
  ya existe, todavia vacia) -- requiere que la ingesta de al menos una
  temporada real ya haya corrido.

### Fase 4 — Calibracion y validacion de varianza (Secciones 8.4.1, 9.2)
- Calibracion isotonica con leave-one-season-out + reliability diagrams,
  ahora si posible en la practica una vez que `jsa/historical/` ingiera
  2022-2026 (antes de esta entrega no habia de donde sacar los datos).
  Mientras no exista, `JSAReport.calibration.calibration_status` se
  mantiene en `"uncalibrated"` y el Confidence Gate nunca pasa -- por
  diseno, no por bug (ver `engine/confidence_gate.py`,
  `engine/decision_engine.py`).
- Validacion de desviacion estandar del margen proyectado vs. la real
  (`ProjectedRunsOutput.variance_validated` se mantiene en `False`).

### Fase 5 — Validacion Cientifica Completa (Seccion 13.1)
- Walk-Forward Validation formal sobre las 5 temporadas.
- Calibration Audit (13.4) -- una vez que exista una curva de calibracion
  real que auditar.
- `JSAReport.monte_carlo_summary` sigue en `None` en el reporte diario:
  el Monte Carlo Audit YA se puede correr (ver arriba), pero conectar su
  resultado mas reciente al reporte de cada juego es el siguiente paso.

### Fase 6 — Confidence Gate y Produccion (Seccion 10.3-10.4)
- Gate Threshold Sweep real sobre >=3 temporadas.
- Los 4 `GateRegistryEntry` sembrados quedan en `status="under_validation"`
  hasta entonces (nunca `validated_70` sin la evidencia exigida).
- Dashboard (Streamlit u otro) -- no existe todavia en esta entrega.

### Fase 7 — Monitoreo Continuo (Secciones 13.6-13.8)
- Drift Detection (PSI, KS Test, ADWIN, Page-Hinkley) -- necesita
  ventanas mensuales de produccion real.
- Calibration Drift mensual.
- Model Card publicada por version (13.7) -- tabla `model_registry` ya
  existe, vacia.
- Quality Gates consolidados (13.8) como veredicto unico pasa/no-pasa.

## Otras limitaciones honestas de esta entrega (fuera de las 7 fases)

- `home_starter_xera`/`xfip`: la MLB Stats API no expone Statcast
  "expected stats" -- se usa ERA real como proxy explicito (ver
  `data_sources/stats.py`). Conectar una fuente Statcast real es un
  candidato natural para una migracion aditiva `3.1 -> 3.2`.
- `lineups_official`, `bullpen_usage_known`, `no_last_minute_changes`,
  `home_closer_available`/`away_...`, `bullpen_ip_last_3_days`,
  `key_injuries`, `travel_distance`: sin fuente de datos wireada, quedan
  en su valor por defecto (nunca inventados) -- esto baja el CRI de forma
  realista. Wirearlos es trabajo de ingesta de datos, no de arquitectura.
- Pilares `trend` (Recent Trend) y `historical` (Historical Favorite
  Context): devuelven `advantage=0` siempre -- no existe todavia una
  fuente de game logs recientes ni de historial head-to-head. Se calculan
  y se reportan (cumpliendo el contrato de Seccion 7.1), pero de forma
  transparente sobre su propia limitacion.
- El criterio 5 del Confidence Gate (Seccion 10.2, "ninguna feature
  dominante tiene Divergence Flag") esta implementado como
  vacuously-true: opera a nivel de feature individual, y ninguna feature
  tiene todavia `real_correlation`/`model_importance` medidos (requiere
  historial). Documentado en `engine/confidence_gate.py`.
- No hay integracion de cuotas de mercado (Odds API) en esta entrega: el
  spec JSA v3.0 no la exige -- el Confidence Gate opera sobre la
  probabilidad calibrada del propio modelo, no sobre edge contra un
  bookmaker. Si en el futuro se quiere una capa de "edge vs. mercado"
  sobre JSA, es una extension natural via Market Registry (Seccion
  10.5bis), no un cambio al nucleo.

## Regla dura para todo lo anterior

Ninguna de estas piezas se agrega editando directamente un registry o un
umbral a mano. Cada una entra por el Scientific Validation Pipeline
completo (Seccion 13): experimento registrado, benchmarking obligatorio,
prueba de significancia, y veredicto de Quality Gates -- igual que exige
el spec para cualquier extension futura (Principio 16).
