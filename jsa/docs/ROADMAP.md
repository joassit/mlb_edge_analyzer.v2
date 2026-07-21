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

## Señal defensiva en `team_quality` (fielding%, `team_quality@1.1.0`)

`team_quality` solo consideraba lesiones clave y `closer_available` --
muy cerca de ser un pilar mudo. Se evaluo agregar OAA (Statcast) o DRS
(Baseball Info Solutions) primero, pero ninguno esta expuesto por
`statsapi.mlb.com` -- OAA vive en Baseball Savant vía endpoints no
documentados sin soporte confiable de filtro point-in-time por equipo,
DRS es propietario sin API publica. Se descartaron ambos a favor de
fielding stats basicos, oficiales y ya validados por un spike real
(`spike_mlb_fielding_and_groundball.yml`, confirmado contra las 5
temporadas): `teams/{id}/stats?stats=byDateRange&group=fielding` expone
`errors`, `doublePlays`, `fielding` (fielding percentage), entre otros,
de forma estable y consistente en todas las temporadas.

**Diseño acordado explicitamente antes de implementar**:
- Se usa `fielding%` solo -- ya viene normalizado por MLB (errores sobre
  chances totales), comparable directo entre equipos sin ajustar por
  volumen de juegos. `errors`/`doublePlays` quedan fuera (`doublePlays`
  depende demasiado de oportunidades/GB rate del staff para ser una señal
  limpia por si sola).
- Ventana: acumulado de temporada point-in-time (mismo patron que
  `team_ops_as_of()`/`bullpen_era_as_of()`, no una ventana de 30 dias --
  con 30 dias la muestra seria demasiado ruidosa).
- Magnitud acotada: la diferencia de fielding% home/away contribuye como
  MAXIMO ±1 nivel (`_FIELDING_PCT_UNIT=0.006`, heuristico de partida
  basado en la variacion observada en el spike, no calibrado todavia),
  sumado aditivamente a lesiones+closer, con el mismo clip final -2..2
  del contrato del pilar -- nunca domina el pilar por si sola.

**Implementacion** (Seccion 3.3, migracion aditiva `schema-3.1-to-3.2` --
`SCHEMA_VERSION` sube de "3.1" a "3.2", campos nuevos `home/away_fielding_pct`
en `GameSnapshot`, `pillar_contract_version` de `team_quality` sube de
`@1.0.0` a `@1.1.0`):
- `historical/point_in_time_provider.py::team_fielding_pct_as_of()` /
  `data_sources/stats.py::get_team_fielding_pct()`: mismo patron exacto
  que `team_ops_as_of()`/`get_team_ops()`, mismo costo de red (una
  llamada por equipo por juego, igual que OPS hoy).
- `engine/pillars/team_quality.py`: nueva logica de blend, ver arriba.

**GB/AO en `starter`**: tambien confirmado disponible en el spike
(`groundOuts`/`airOuts`/`groundOutsToAirouts` vienen en el MISMO payload
que ya se pide para ERA/IP, costo de red cero) -- explicitamente NO
agregado en esta entrega. El diagnostico ya establecio que el problema
principal de `starter` es el shrinkage agresivo (`SHRINKAGE_K_IP=60`) mas
que falta de datos; agregar GB% ahora, antes de revisar shrinkage,
mezclaria dos tipos de cambio distintos sin poder atribuir despues que
movio los resultados. Ni siquiera se captura el campo sin usar -- mismo
criterio de no dejar datos sin logica real detras. Se revisa junto con el
shrinkage de `starter`/`bullpen`, despues de ver los resultados de esta
re-ingesta con `team_quality` ya activo.

**Bundle final de la re-ingesta combinada de las 5 temporadas**: Context
(travel_distance + weather_wind_speed) + `team_quality` (lesiones +
closer_available + fielding%) + `starter_projected_ip`. Cierre explicito
del alcance -- no se agrega nada mas antes de disparar la re-ingesta.

## `run_season_ingestion(..., force=True)` -- necesario para que la re-ingesta sea real

Las 5 temporadas ya estaban completamente ingeridas ANTES de esta serie de
arreglos. `already_ingested_game_pks()` (pensada para resumir una corrida
cortada por un timeout de GitHub Actions, no para forzar un reproceso)
hubiera saltado de largo cada juego ya presente en `historical_report` --
disparar la ingesta tal cual habria procesado CERO juegos, dejando toda la
logica nueva sin aplicar a los datos ya persistidos. Ademas, el
`UniqueConstraint` de `historical_snapshot` (via `insert_ignore_duplicates`)
habria ignorado en silencio cualquier insert nuevo mientras el snapshot
VIEJO siguiera ahi.

Se agrego `historical_db.clear_season(engine, season)` -- borra
`historical_snapshot`/`historical_report`/`historical_season_run` de una
temporada, **nunca** `historical_game` (schedule/resultados son hechos
estables que no cambian con la logica de evaluacion) -- y un flag
`force=True` en `run_season_ingestion()` (`--force` en `cli.py`, input
`force` en `jsa_historical_ingest.yml`) que lo invoca antes de ingerir.
`False` por default en los tres niveles -- nunca borra datos sin que se
pida explicitamente, y el paso de borrado queda logueado (cuantas filas se
borraron) antes de reprocesar.

## Re-ingesta real de las 5 temporadas (Context + team_quality + starter_projected_ip)

Disparada con `force=True` -- resultado: 13,116 juegos totales, 13,115
procesados, 1 error (99.99%). Confirma que `clear_season()` funciono
exactamente como se diseño (cada log muestra el borrado previo con el
mismo conteo de filas que la ingesta anterior, antes de reprocesar desde
cero).

| Temporada | Juegos totales | Procesados | Errores |
|---|---|---|---|
| 2022 | 2740 | 2739 | 1 |
| 2023 | 2894 | 2894 | 0 |
| 2024 | 2841 | 2841 | 0 |
| 2025 | 2841 | 2841 | 0 |
| 2026 (a la fecha) | 1800 | 1800 | 0 |

**Bug real encontrado y corregido**: el unico error (2022) fue
`ValueError: could not convert string to float: '-.--'` en
`pitcher_era_ip_as_of()` -- la MLB Stats API a veces devuelve el string
literal `"-.--"` como ERA cuando es indefinido (ej. un pitcher con 0 IP
pero una carrera cargada), un placeholder no numerico que `float()`
rechaza. Bug preexistente (la misma vulnerabilidad ya existia antes de
esta serie de cambios), nunca disparado hasta esta corrida real. El
manejo de errores por-juego de `run_season_ingestion()` lo contuvo
correctamente -- solo ese juego quedo sin reporte, la temporada completa
NO abortó.

Corregido con `point_in_time_provider.py::_parse_era()` -- parsea el ERA
de forma defensiva (`None` si no es numerico) en los DOS puntos donde
`float(era)` corria fuera de un `try/except` que ya lo cubriera:
`pitcher_era_ip_as_of()` y `bullpen_era_as_of()` (este ultimo ahora
ademas ignora solo al pitcher con ERA indefinido dentro del loop de
relevistas, en vez de arriesgarse a abortar el calculo de todo el
bullpen). `league_averages_as_of()` ya tenia esta clase de parseo
protegido por su propio `try/except` externo -- no necesitaba el mismo
arreglo.

## `jsa_historical_validate_direct.yml` -- validacion directa contra Postgres

`jsa_historical_validate.yml` (fusion de artifacts SQLite por temporada)
quedo obsoleto para este caso: las 5 temporadas ya viven juntas en
`JSA_HISTORICAL_DATABASE_URL` (Postgres), no hace falta descargar ni
fusionar nada. Nuevo workflow, mismo patron que
`jsa_historical_pillar_contribution.yml` -- corre el subcomando
`validate` de `cli.py` (benchmark_season + Monte Carlo Audit +
PillarContributionAnalyzer juntos) directo contra la base ya unificada.

## Resultados reales de `validate` sobre las 5 temporadas re-ingeridas

Corrido via `jsa_historical_validate_direct.yml` (200 simulaciones de
Monte Carlo por temporada) inmediatamente despues de la re-ingesta con
Context + `team_quality` (lesiones + closer + fielding%) +
`starter_projected_ip` -- la evidencia real que motiva el arreglo de
shrinkage de bullpen de la seccion siguiente:

- **`team_quality` dejo de ser mudo**: `zero_advantage_rate` bajo de
  100% (en las 5 temporadas, antes de esta serie de arreglos) a
  26-38% -- ahora mueve el Evidence Score en 62-74% de los juegos, con
  una contribucion media de 15-19%, en el mismo rango que `starter`
  (17-19%) y no muy lejos de `offense` (21-25%)/`bullpen` (25-26%).
- **`context` mejoro solo marginalmente** (100% -> ~97-98% zero-advantage,
  contribucion media 0.5-0.8%) -- esperado: solo dispara en condiciones
  extremas (viaje/clima extremo, dobles carteleras, 2+ lesiones
  combinadas) y tiene el segundo peso base mas chico (0.08).
- **`bullpen` sigue dominando la mayoria de las temporadas**:
  `most_dominant_pillar` en 4/5 temporadas, `critical_failure_factor` de
  Monte Carlo en 4/5 temporadas, `dominance_warning_rate` 25-28% --
  consistente con el diagnostico ya hecho (sin shrinkage, unit mas chico,
  peso base mas alto), sin tocar en esta re-ingesta. Motiva la seccion
  siguiente.
- **Home bias favoritism rechazado en las 5 temporadas** (exceso de
  favoritismo local 3.7-9.5pp, umbral 3pp) -- hallazgo de gobernanza
  independiente de esta serie de arreglos, no abordado todavia.
- Accuracy 53-56%, Brier 0.269-0.283 (el baseline `naive_constant` logra
  ~0.248-0.249) -- consistente con el estado `"uncalibrated"` ya
  reportado honestamente por el sistema.

## Shrinkage de `bullpen` (`bullpen@1.1.0`) -- cierra la asimetria con `starter`

Diagnostico exacto: `bullpen.py` comparaba el ERA CRUDO de bullpen
(`snapshot.home_bullpen_era` directo, sin ninguna llamada a
`shrunk_era()`) -- el UNICO de los 7 pilares que nunca encogia su
estadistica hacia el promedio de liga por muestra chica. Combinado con
`_UNIT_ERA_RUNS=0.45` (mas sensible que el `0.55` de `starter`) y el
peso base mas alto de los 7 pilares (`0.25`), esto amplifica ruido
temprano de temporada (ERAs de bullpen extremos con muestras de IP muy
chicas) en vez de suavizarlo -- exactamente el patron que muestran los
resultados de arriba.

Causa raiz de por que nunca tuvo shrinkage: `shrunk_era()` necesita una
muestra de IP, y `GameSnapshot` nunca tuvo un campo
`home/away_bullpen_ip_sample` -- la plomeria de datos quedo incompleta,
no fue una decision deliberada. Tanto `bullpen_era_as_of()` (historico)
como `get_bullpen_era()` (produccion) YA calculaban `total_ip`
internamente en el mismo loop que ya calcula el ERA ponderado -- solo
faltaba exponerlo, **cero llamadas de red adicionales**.

**Cambios** (migracion aditiva `schema-3.2-to-3.3`, `SCHEMA_VERSION`
3.2 -> 3.3, `bullpen@1.0.0` -> `1.1.0`):
- `home/away_bullpen_ip_sample` nuevos en `GameSnapshot`.
- `bullpen_era_as_of()`/`get_bullpen_era()` devuelven `"ip"` ademas de
  `"era"`/`"closer_pitcher_id"`.
- `engine/pillars/bullpen.py`: aplica `shrunk_era()` con el MISMO
  `SHRINKAGE_K_IP=60` compartido que ya usa `starter` -- ninguna
  constante nueva, ambos sin calibrar todavia contra historial propio.

**Cambio de comportamiento deliberado**: al adoptar el patron exacto de
`starter.py`, el fallback para un equipo sin ERA de bullpen tambien
cambio -- antes usaba el ERA del RIVAL como proxy (asumir paridad
implicita); ahora usa el promedio de liga, igual que `starter`, un
criterio consistente entre pilares en vez de un hack especifico de
bullpen.

**Deliberadamente NO tocado en este cambio** (medir una variable a la
vez, mismo criterio de todo este diagnostico): `_UNIT_ERA_RUNS` de
bullpen se deja en `0.45` -- se mide el efecto del shrinkage solo primero
via `PillarContributionAnalyzer` antes de decidir si todavia hace falta
tocarlo. `BASE_PILLAR_WEIGHTS` (bullpen `0.25` vs starter `0.22`) se
deja completamente fuera -- es una decision global entre los 7 pilares
que pertenece a la fase de calibracion formal (Seccion 8.4.1/9.2), no a
esta revision de shrinkage.

## Calibracion isotonica de `evidence_score_raw` -- ajuste + validacion (Fase 4 parcial, Seccion 8.4.1/9.2)

**Hallazgo critico antes de construir nada**: `CalibrationInfo.raw_probability`
(lo unico que existia para calibrar) viene de `skellam_win_prob(mu_home,
mu_away)` en `orchestrator.py` -- el modelo de Projected Runs (Seccion 9),
un modulo COMPLETAMENTE SEPARADO del Evidence Engine de 7 pilares que
toda esta serie de arreglos (Context, `team_quality`, shrinkage de
bullpen/starter) vino mejorando. `evidence_score_raw` (la suma ponderada
de advantages, Seccion 8.1) nunca se convertia en probabilidad en ningun
lado. Calibrar el `raw_probability` existente hubiera significado
calibrar un modelo que nunca usa `team_quality`, Context, ni el fix de
bullpen.

**Decision**: `raw_probability` pasa a derivarse de `evidence_score_raw`
(reemplaza al valor Skellam-derivado como fuente de calibracion/decision;
Skellam/Projected Runs sigue existiendo como modulo de comparacion, igual
que ya se usa en `historical/validation.py::_legacy_predictions`). Sin
transformacion logistica intermedia: isotonic regression no necesita que
su entrada YA sea una probabilidad, solo que este monotonamente
relacionada con el resultado -- se ajusta
`IsotonicRegression(evidence_score_raw -> P(home wins))` directo,
evitando inventar una sigmoide sin calibrar como paso intermedio.

**Esta entrega es SOLO la infraestructura de ajuste + validacion --
deliberadamente NO wireada todavia a `orchestrator.py`** (ver seccion
siguiente de "NO construido"). `JSAReport.calibration.calibration_status`
sigue en `"uncalibrated"` hasta una entrega separada, con su propia
revision explicita -- pasar el Confidence Gate de "nunca pasa" a
"puede pasar" es un cambio de comportamiento demasiado grande para
mezclar con la construccion del ajuste en si.

**Cambios**:
- `scikit-learn==1.9.0` nuevo en `jsa/requirements.txt` (`IsotonicRegression`;
  `scipy` ya presente no trae un transformer equivalente con
  interpolacion/`predict()` listo para usar).
- `registries/db.py::calibration_registry` (tabla nueva, append-only,
  mismo patron que `gate_registry`) + `domain/models.py::CalibrationRegistryEntry`
  -- persiste los knots de la curva de PRODUCCION (`x_knots`/`y_knots`,
  ajustada sobre TODAS las temporadas pedidas) y las metricas LOSO
  agregadas (`loso_brier`/`loso_log_loss`/`loso_accuracy`/`loso_ece`/`loso_mce`).
  `status="validated"` es la UNICA condicion que una entrega futura puede
  usar para pasar `calibration_status` a `"calibrated"` -- nunca a mano.
- `historical/calibration.py::fit_and_validate(seasons, db_url)`: leave-
  one-season-out real -- cada temporada se evalua SOLO con un modelo
  ajustado sobre las demas (nunca la curva de produccion evaluada contra
  sus propios datos de entrenamiento, eso seria un numero optimista).
  `status`: `"validated"` si `>=3` temporadas pasan walk-forward (cada
  una con `>=50` juegos, Seccion 10.4), `"rejected_insufficient_data"` si
  ninguna temporada alcanza el minimo, `"under_validation"` en el medio.
  Reusa `brier_score()`/`log_loss()`/`accuracy()`/`ece()`/`mce()` de
  `historical/validation.py` -- cero logica de metricas duplicada.
- `historical/cli.py::calibrate` + `.github/workflows/jsa_historical_calibrate.yml`
  (mismo patron shell-conditional de secrets que `jsa_historical_ingest.yml`
  -- lee de `JSA_HISTORICAL_DATABASE_URL`, persiste en `JSA_DATABASE_URL`,
  donde vive el resto de los registries).

240 tests pasan (8 nuevos, `test_calibration.py`, incluyendo una prueba
end-to-end de que una relacion perfectamente monotona sin ruido produce
un `loso_brier < 0.05`).

**Resultado real** (`jsa_historical_calibrate.yml` corrido contra las 5
temporadas en Postgres, 13,099/13,116 juegos con resultado valido):
`status="validated"` (5/5 temporadas pasaron walk-forward), `loso_brier=0.2452`,
`loso_log_loss=0.6835`, `loso_accuracy=55.38%`, `loso_ece=0.00298`,
`loso_mce=0.1382`. La curva esta **muy bien calibrada** (ECE casi cero)
pero **discrimina poco**: el Brier/log loss quedan apenas por debajo del
piso de un modelo sin skill (p=0.5 constante: Brier=0.25, log loss=0.693),
y el accuracy apenas supera la ventaja de local pura de las Mayores
(~54%). Motivo de la seccion siguiente.

## Auditoria de poder discriminativo del Evidence Score (`historical/discriminative_audit.py`)

Seguimiento directo al resultado real de arriba -- ¿por que el Evidence
Score calibra tan bien pero discrimina tan poco? Modulo de solo lectura:
no modifica `pillars/`, `engine/`, `calibration_registry` ni el pipeline,
solo lee `historical_report`/`historical_snapshot` ya ingeridos. Toda
comparacion de escenarios (ablacion, pesos alternativos, shrinkage
alternativo) reusa `calibration.py::loso_fit_and_score()` (extraido de
`fit_and_validate()` sin cambiar su comportamiento) -- nunca un split
distinto que pudiera inflar una mejora artificialmente, y cada delta se
acompana de un intervalo de confianza (bootstrap pareado 90%, 500
remuestreos sobre las predicciones LOSO ya calculadas) para no aceptar
una mejora que cruce cero.

**Cambios**:
- `historical/calibration.py::loso_fit_and_score()` (nuevo, extraido de
  `fit_and_validate()` -- mismo comportamiento, ahora reusable).
- `historical/discriminative_audit.py` (nuevo) -- 8 fases: (1) AUC/KS/MI/
  correlacion-con-resultado/PSI-entre-temporadas/permutation-importance
  por pilar; (2) matrices de correlacion Pearson/Spearman/MI entre los 7
  pilares; (3) ablacion LOSO quitando un pilar a la vez (pesos de los 6
  restantes renormalizados a sumar 1), clasificado
  imprescindible/util/neutro/perjudicial segun si el IC del delta de
  Brier cruza cero; (4) optimizacion de `BASE_PILLAR_WEIGHTS` con
  `scipy.optimize.differential_evolution` (parametrizado via softmax --
  `>=0` y suma`=1` automaticos), objetivo `loso_log_loss`; (5) distribucion
  de `evidence_score_raw` (percentiles, skew, kurtosis, histograma); (6)
  separabilidad ganados-vs-perdidos (KS, Cohen's d, divergencia
  Jensen-Shannon, overlap); (7) ROC/Precision-Recall/Lift/Gain/reliability
  diagram binned, TODOS sobre predicciones LOSO out-of-sample (nunca de
  entrenamiento); (8) sensibilidad de `SHRINKAGE_K_IP` en starter+bullpen
  (`k=0` sin encoger, `k=20` reducido, `k=60` actual), recalculando el
  advantage discreto desde los campos crudos de `GameSnapshot` ya
  persistidos en `historical_snapshot` (sin volver a golpear la API).
- **Nota de alcance de la Fase 4**: el vector de pesos candidato se aplica
  de forma ESTATICA e identica a todos los juegos (el mismo rol que
  cumple `BASE_PILLAR_WEIGHTS`) -- no vuelve a correr el Rule/Weight
  Engine por juego (Seccion 6), que aplicaria deltas de contexto por
  encima de esa base. Reconstruir eso exigiria re-evaluar el Context
  Detector + Rule Engine para cada juego historico, fuera del alcance de
  esta auditoria (que solo lee reportes ya persistidos).
- **Fix de fuga de informacion en la Fase 4** (encontrado en la revision
  del usuario antes del merge, no en la construccion original): la
  primera version de `optimize_weights()` elegia los pesos minimizando el
  `loso_log_loss` agregado sobre las 5 temporadas, y reportaba ese MISMO
  numero como "mejora" -- sesgo de seleccion (analogo a reportar el score
  de un k-fold CV usado para elegir hiperparametros como si fuera
  generalizacion; cada prediccion individual es out-of-fold, pero el
  vector ganador fue elegido mirando el desempeno en las 5 temporadas, sin
  dejar ninguna realmente no vista para validar esa eleccion). Se agrego
  `optimize_weights_nested()`: LOSO anidado -- por cada temporada externa,
  los pesos se optimizan usando SOLO las 4 restantes (su propio LOSO
  interno como objetivo de `differential_evolution`) y se evaluan en la
  externa con una curva isotonica ajustada UNICAMENTE sobre esas 4, nunca
  vista durante esa busqueda de pesos. `optimize_weights()` (renombrada
  internamente su intencion, no su firma) sigue existiendo para producir
  un unico vector desplegable ajustado con toda la evidencia, pero su
  propio numero de mejora ahora viene marcado con un `"warning"` explicito
  en el resultado -- la pregunta "¿la mejora es real?" la responde
  `optimize_weights_nested()`, nunca la version de produccion.
- `historical/cli.py::discriminative-audit` + `.github/workflows/jsa_historical_discriminative_audit.yml`
  (timeout de 90 min -- la Fase 4 corre DOS optimizaciones completas,
  la de produccion y la anidada, la mas lenta de las 8 fases).
- Sin dependencias nuevas: `scipy.optimize.differential_evolution` (ya en
  requirements) cubre el algoritmo de optimizacion pedido sin agregar
  Optuna.

13 tests nuevos (`test_discriminative_audit.py`), datos sinteticos con
relacion real (con ruido) entre pilares y resultado, incluyendo una
prueba dedicada de que `optimize_weights_nested()` produce un vector de
pesos propio por cada fold externo (nunca ajustado con la temporada que
luego evalua). **Sin correr todavia contra Postgres real** -- pendiente
de review/merge de este PR y un dispatch posterior, igual que `calibrate`.

## Pre-vuelo antes del primer dispatch real: metadata/timing/memoria (Fase 4 nested)

Revision del usuario antes de correr `jsa_historical_discriminative_audit.yml`
contra Postgres, checklist item por item contra el codigo (no de memoria):
5 de 7 puntos ya se cumplian por construccion (dataset congelado -- solo
lee tablas ya ingeridas; pitchers/fuente estables por el mismo motivo;
semilla fija en `differential_evolution` y en todo `np.random` del
modulo; `optimize_weights_nested()` realmente sin sesgo de seleccion;
JSON con todas las fases, no solo un resumen). **2 puntos NO se
cumplian** -- se agregaron antes de correr nada:

- `run_full_audit()["run_metadata"]`: `commit_sha` (`GITHUB_SHA` si corre
  en Actions, si no `git rev-parse HEAD`), `generated_at_utc`, y `config`
  completo (temporadas pedidas, seeds/maxiter/popsize de ambas
  optimizaciones, `MIN_GAMES_PER_SEASON`/`MIN_SEASONS_FOR_WALK_FORWARD`,
  `BASE_PILLAR_WEIGHTS`, los 3 valores de `SHRINKAGE_K_IP` auditados) --
  para poder saber, mirando solo el JSON persistido, exactamente que
  version de codigo y que parametros lo produjeron.
- `phase_timings_seconds`/`phase_peak_rss_kb` por fase (las 8 fases +
  ambas optimizaciones de la Fase 4 por separado) -- `ru_maxrss` es un
  high-water-mark acumulado (nunca memoria aislada de una fase sola,
  documentado asi en el docstring de `_peak_rss_kb()`), pero alcanza para
  ver si una fase hace subir el techo de memoria.
- Pedido adicional del usuario, ya semi-cubierto: `optimize_weights_nested()`
  ya devolvia `per_season_optimized_weights` (pesos finales por fold);
  se le agrego `optimizer_n_function_evaluations` y `fold_seconds` por
  fold -- para poder ver si alguna temporada converge a pesos o costo muy
  distintos de las demas (senal de inestabilidad, no solo de "temporada
  dificil").

## Resultado real de `jsa_historical_discriminative_audit.yml` (5 temporadas, 13,099 juegos)

Corrida real contra Postgres (`commit_sha=d7c0d6c`), 5m12s. Hallazgos:

- **Confirmado**: 10% del peso (`trend`+`historical`) va a pilares stub
  con `advantage=0` en el 100% de los 13,099 juegos -- peso
  completamente desperdiciado.
- **Confirmado**: `context` tiene AUC=0.500 y solo usa 2 de 5 niveles
  posibles (298 de 13,099 juegos usan `-1`, el resto `0`) -- señal
  estadisticamente real en la ablacion (IC no cruza cero) pero
  economicamente insignificante.
- **Confirmado**: el Evidence Score esta comprimido al centro -- 54.5%
  de los juegos caen en evidence_score ∈ [-0.5, 0.5]; tras calibrar, 51%
  de las probabilidades predichas caen en la banda 46.7%-53.3%.
- **Descartado**: la hipotesis de que el shrinkage es "demasiado
  agresivo" -- `k=60` (actual) supera a `k=20` y a `k=0` en LOSO Brier,
  diferencia estadisticamente significativa en ambos casos (Fase 8).
- **`optimize_weights_nested()` real**: `generalizes: false` -- los
  pesos optimizados NO mejoran fuera de muestra (de hecho empeoran
  ligeramente, Brier 0.24547 vs 0.24523 actual), y ademas
  `optimizer_converged=False` en los 5 folds externos + la corrida de
  produccion -- con solo 5 temporadas y 2 pilares matematicamente
  irrelevantes (trend/historical, cuyo peso no cambia el ranking de
  ningun juego bajo isotonic regression), el espacio de busqueda de 7
  pesos no converge de forma confiable con el presupuesto usado.
- Ranking de pilares por contribucion real (AUC + ablacion LOSO,
  concuerdan): offense > bullpen > starter > team_quality > context >>
  trend = historical (muertos).

Informe completo (6 graficos: ranking de pilares, heatmap de
correlaciones, reliability diagram + histograma, ROC, sensibilidad de
shrinkage, inestabilidad de pesos por fold) entregado directamente al
usuario -- no archivado como markdown aqui para no duplicar; el JSON
crudo del run queda en el artifact de GitHub Actions
(`jsa-historical-discriminative-audit-29488448243`, 30 dias de retencion).

## Segunda generacion: `historical/resolution_audit.py` -- que se pudo responder sin nueva ingesta, y que no

Tras el resultado de arriba, el pedido de investigacion de segunda
generacion planteaba 9 fases (Trend/Historical reales, discretizacion,
Team Quality profundo, Evidence Score continuo, integracion). Antes de
escribir una sola linea de codigo, se audito cual de esas fases es
respondible con evidencia real SIN nueva infraestructura:

**Bloqueado, no construido, y por que (no se fabrico ningun numero para esto)**:
- **Trend/Historical reales (Fases 1-2 del pedido)**: este sandbox no
  tiene salida de red a `statsapi.mlb.com` -- cualquier campo nuevo
  exige nuevos metodos de provider + migracion de schema aditiva +
  **re-ingerir las 5 temporadas** (horas de GitHub Actions por
  temporada, ya vivido en esta sesion). Ademas, varios candidatos
  propuestos (`wRC+`, `xFIP`, `WAR`) son metricas de FanGraphs, nunca
  integradas en este proyecto -- no hay forma de confirmar viabilidad
  sin decision explicita del usuario sobre esa fuente de datos.
- **BaseRuns y WAR para team_quality (parte de la Fase 7 del pedido)**:
  mismo problema -- requieren datos (batted-ball, valor defensivo) que
  este proyecto no tiene ingeridos.
- **Fase 9 (integracion)**: prematura hasta resolver lo anterior.

**Si construido, con datos 100% ya ingeridos (nunca golpea la API)**:
- `historical/resolution_audit.py::run_discretization_sweep()` (Fases 3+8
  del pedido): 6 configuraciones -- `A` (actual, -2..2), `B` (-3..3), `C`
  (-4..4), `D` (percentiles), `E` (z-score continuo), `F` (diff crudo sin
  discretizar) -- aplicadas UNICAMENTE a starter/bullpen/offense (los 3
  pilares con un diff continuo subyacente limpio, reconstruido desde los
  campos crudos de `GameSnapshot` ya en `historical_snapshot`, misma
  formula exacta que produccion). `team_quality`/`context`/`trend`/
  `historical` NUNCA se tocan en el sweep -- se aisla el efecto de la
  resolucion de esos 3 pilares. Cada configuracion corre LOSO completo +
  bootstrap pareado contra la configuracion `A` (actual).
- `historical/resolution_audit.py::compute_elo_and_pythagorean()` +
  `evaluate_team_quality_alternatives()` (Fase 7 parcial): Elo (reinicia
  en 1500 cada temporada, K=20 -- simplificacion documentada, no oculta)
  y Pythagorean Expectation (exponente 1.83), ambos calculados
  point-in-time-safe por DIA calendario (nunca por juego individual
  dentro del mismo dia, ya que `historical_game` no guarda hora exacta)
  a partir de `historical_game` ya ingerido. Nunca reemplazan
  `team_quality` en produccion -- solo se MIDE que pasaria si se
  sustituyera (mismo peso, valor z-scoreado), comparado via bootstrap
  contra dejarlo como esta.
- Verificacion anti-fuga explicita: `compute_elo_and_pythagorean()` se
  probo con resultados 100% aleatorios (moneda pura, sin relacion con la
  identidad del equipo) -- AUC de `elo_diff` da ~0.49 (sin señal, como
  debe ser); y con una diferencia de habilidad real embebida a proposito
  -- AUC claramente por encima de 0.5 (se recupera la señal real). Ambos
  tests en `test_resolution_audit.py`.
- `historical/cli.py::resolution-audit` + `.github/workflows/jsa_historical_resolution_audit.yml`
  (solo lee `JSA_HISTORICAL_DATABASE_URL`, no necesita `JSA_DATABASE_URL`
  -- no persiste nada, es de solo lectura).

8 tests nuevos (`test_resolution_audit.py`, ampliados con
`per_season_metrics` en el sweep de discretizacion y en las
alternativas de team_quality tras la revision del usuario antes de
mergear -- ver punto siguiente). **Sin correr todavia contra Postgres
real** -- pendiente de review/merge de este PR.

## Revision del usuario antes de mergear PR #24 -- 1 de 4 condiciones no se cumplia

Checklist pedido: (1) ausencia de data leakage en Elo/Pythagorean, (2)
recalibracion independiente por configuracion del sweep, (3) metricas
por temporada ademas del agregado, (4) documentacion explicita de
limitaciones. Verificado contra el codigo, no de memoria:

- **(1) y (2) ya se cumplian**: `compute_elo_and_pythagorean()` procesa
  por dia calendario (nunca por juego dentro del mismo dia) y solo lee
  el estado ANTES de actualizarlo -- releido linea por linea para
  confirmar; `run_discretization_sweep()`/`evaluate_team_quality_alternatives()`
  llaman `calibration.loso_fit_and_score()` una vez por configuracion/
  candidato, cada uno con su propio ajuste isotonico fresco, nunca
  reutilizado entre configuraciones.
- **(3) NO se cumplia**: `loso_fit_and_score()` ya calculaba
  `per_season_metrics` internamente, pero `run_discretization_sweep()` y
  `evaluate_team_quality_alternatives()` lo descartaban al armar su
  resultado final -- solo devolvian el agregado de las 5 temporadas. Se
  corrigio antes de mergear: ambas funciones ahora incluyen
  `per_season_metrics` (y `evaluate_team_quality_alternatives()` tambien
  expone `current_team_quality_per_season_metrics` para comparar peras
  con peras).
- **(4) ya se cumplia** -- ver seccion anterior.

## Resultado real de `jsa_historical_resolution_audit.yml` (5 temporadas, 13,099 juegos)

Sweep de discretizacion (starter+bullpen+offense) -- ninguna alternativa
mejora la actual (`-2..2`), y las dos versiones continuas empeoran de
forma estadisticamente significativa:

| Config | LOSO Brier | Δ vs. actual | Significativo |
|---|---|---|---|
| A (actual, -2..2) | 0.24610 | -- | -- |
| B (-3..3) | 0.24617 | +0.0000752 | No |
| C (-4..4) | 0.24613 | +0.0000345 | No |
| D (percentiles) | 0.24630 | +0.000206 | No |
| E (z-score continuo) | 0.24716 | +0.001062 | **Si, peor** |
| F (diff crudo continuo) | 0.24746 | +0.001356 | **Si, peor** |

**Conclusion**: la discretizacion actual NO destruye informacion -- si
acaso, actua como regularizador (mas granularidad o continuidad empeora).

Team Quality: Elo y Pythagorean Expectation tienen mejor AUC individual
standalone (0.559 ambos) que team_quality actual (0.532), pero
SUSTITUIR team_quality por cualquiera de los dos no mejora el Evidence
Score completo -- Pythagorean empeora de forma significativa
(`delta_brier=+0.000574`, IC 0.000113 a 0.001078), Elo no cambia de
forma distinguible del ruido (`delta_brier=+0.000301`, IC cruza cero).
Correlacion moderada preexistente entre team_quality y ambas
alternativas (0.27 con Elo, 0.21 con Pythagorean) sugiere que
team_quality ya captura parcialmente la misma dimension -- sustituirlo
reorganiza la señal, no la aumenta.

**Ninguna de las dos hipotesis de esta segunda generacion (discretizacion
subotima, Team Quality reemplazable) sobrevivio a la evidencia real.**
El techo de discriminacion no esta en como se combinan/discretizan las
señales existentes -- sigue apuntando a que falta señal nueva (Trend/
Historical reales, o pilares completamente nuevos).

## Tercera generacion: candidatos de forma reciente para Trend (schema 3.3 -> 3.4)

Tras el resultado de arriba, y siguiendo el roadmap estrategico del
usuario ("la etapa de ingenieria del modelo esta completa -- la que
sigue es ingenieria de la informacion"), el primer paso concreto es
implementar Trend de verdad (hoy un stub, `advantage=0` en el 100% de
los juegos). El pedido original listaba 7 candidatos (incluyendo wRC+/
xFIP, de FanGraphs) -- se acoto a 4 para la primera iteracion, todos
calculables con el mismo patron `byDateRange` que ya usa el resto del
proveedor (sin infraestructura nueva de fetching, sin decidir todavia
sobre FanGraphs/Statcast/Retrosheet -- eso queda para una Fase 3
separada si estos 4 no alcanzan):

- OPS de equipo, ventana movil 7 dias
- OPS de equipo, ventana movil 14 dias
- ERA de equipo (pitching agregado, no solo abridores), ventana movil 7 dias
- ERA de equipo, ventana movil 14 dias

**Esta entrega SOLO agrega la infraestructura de recoleccion -- `trend.py`
sigue siendo un stub, sin cambios.** Cumpliendo la instruccion explicita
del usuario ("cada candidato debera competir bajo LOSO, nunca asumir que
uno es mejor sin evidencia"): no tiene sentido comprometerse a una
formula todavia, cuando ninguno de los 4 candidatos tiene datos reales
con los que compararse. La secuencia correcta es: (1) esta entrega
recolecta los 4 candidatos en cada snapshot re-ingerido; (2) re-ingesta
de las 5 temporadas (horas de GitHub Actions, requiere confirmacion
explicita del usuario antes de disparar, igual que siempre); (3) un
modulo de analisis nuevo (mismo patron que `discriminative_audit.py`/
`resolution_audit.py`) mide AUC/MI/KS/PSI individual de cada candidato Y
el LOSO resultante de activarlo en `trend.py` con parte del 5% de peso
hoy desperdiciado; (4) solo el/los candidato(s) que mejoren LOSO Brier/
LogLoss de forma significativa se wirean en `trend.py` (PR separado,
con su propio bump de `pillar_contract_version`).

**Cambios**:
- `domain/models.py`: `SCHEMA_VERSION` 3.3 -> 3.4 (`schema-3.3-to-3.4`,
  additivo). 8 campos nuevos: `home/away_team_ops_rolling_7d`,
  `home/away_team_ops_rolling_14d`, `home/away_team_era_rolling_7d`,
  `home/away_team_era_rolling_14d`.
- `historical/point_in_time_provider.py::team_ops_rolling_as_of()` /
  `team_era_rolling_as_of()` -- mismo patron `byDateRange` que
  `team_ops_as_of()`/`hitter_recent_pa_as_of()`, con `startDate = as_of_date - days`
  en vez de acumulado de temporada. `_parse_era()` ya protege el placeholder
  `"-.--"` de la API, reusado sin cambios.
- `historical/snapshot_reconstruction.py` -- 8 llamadas nuevas al
  provider (2 metricas x 2 ventanas x 2 equipos), pasadas a
  `build_game_snapshot()`.
- `registries/seed.py::_seed_schema_migration()` -- entrada
  `schema-3.3-to-3.4`.

10 tests nuevos (`test_rolling_stats_provider.py`: verifica ventana de
fecha exacta contra la API real mockeada, nunca incluye el dia de corte;
`test_historical_point_in_time.py`: wiring de los 8 campos nuevos en el
snapshot con `FakeProvider`). **Sin re-ingesta todavia** -- pendiente de
review/merge de este PR y de la confirmacion explicita del usuario antes
de disparar `jsa_historical_ingest.yml --force` sobre las 5 temporadas.

## Pre-vuelo antes de la re-ingesta de Trend: checklist del usuario (4 puntos, 3 no se cumplian)

Antes de disparar la re-ingesta, el usuario pidio confirmar: (1) congelar
la linea base actual para poder comparar antes/despues; (2) versionar la
corrida (commit SHA, schema version, version del proveedor); (3)
validaciones automaticas post-ingesta por temporada (cobertura de
snapshots, cobertura de los campos nuevos, consistencia de fechas), que
detengan el proceso si fallan; (4) que la re-ingesta no toque pesos,
calibracion, discretizacion ni shrinkage. Verificado contra el codigo:

- **(4) ya se cumplia** -- confirmado con `git diff --stat` del PR: cero
  cambios en `config.py` (`BASE_PILLAR_WEIGHTS`/`SHRINKAGE_K_IP`),
  `engine/pillars/*` (discretizacion) o `historical/calibration.py`.
- **(1), (2) y (3) NO se cumplian** -- se agregaron antes de re-ingerir:
  - **(1)** `jsa/docs/baselines/pre_trend_2026-07-16/` (nuevo, commiteado
    al repo -- nunca solo un artifact de GitHub Actions con 30 dias de
    retencion): copia real de `discriminative_audit_result.json` y
    `resolution_audit_result.json` (los resultados YA obtenidos de las
    corridas reales contra Postgres), mas un `README.md` explicando como
    usarlos para comparar antes/despues.
  - **(2)** `historical/db.py::historical_ingestion_run_metadata` (tabla
    NUEVA, nunca `ALTER TABLE` sobre `historical_season_run` ya existente
    en el historico real -- `create_all()` no agrega columnas a una tabla
    que ya existe). Cada corrida de `run_season_ingestion()` inserta una
    fila con `commit_sha` (`GITHUB_SHA` en Actions, `git rev-parse HEAD`
    si no), `schema_version` (`domain.models.SCHEMA_VERSION`),
    `provider_version` (nuevo `point_in_time_provider.PROVIDER_VERSION`,
    primera vez que el proveedor se versiona explicitamente -- `1.0.0`
    implicito para todo lo anterior, `1.1.0` desde que existen
    `team_ops_rolling_as_of()`/`team_era_rolling_as_of()`), y si fue
    `--force`.
  - **(3)** `historical/ingestion_validation.py::validate_season_ingestion()`
    (nuevo, solo lectura) + `historical/cli.py validate-ingestion` +
    paso nuevo en `jsa_historical_ingest.yml` (`if: always()`, corre
    siempre que la ingesta haya terminado) -- verifica cobertura de
    snapshots (`>=90%` de los juegos con resultado), cobertura de cada
    uno de los 8 campos rolling de Trend (`>=70%` no-nulos -- umbral
    generoso a proposito: los primeros dias de temporada NO tienen
    ventana de 7/14 dias completa todavia, eso es esperado, no un bug),
    y consistencia de `game_date` entre `historical_game`/
    `historical_snapshot`. `sys.exit(1)` si alguna falla -- el step (y
    por lo tanto el job de esa temporada) queda en rojo, deteniendo el
    proceso antes del siguiente dispatch.

16 tests nuevos (`test_ingestion_validation.py`: 6 casos incluyendo
cobertura baja de snapshots, cobertura baja de un campo especifico,
tolerancia a nulos parciales esperados de inicio de temporada,
inconsistencia de `game_date`; `test_historical_pipeline.py`: 1 caso
nuevo verificando que `historical_ingestion_run_metadata` se escribe con
los valores correctos).

## Re-ingesta real de las 5 temporadas (2022-2026) + drift entre corridas

Tras el merge del PR #26, se dispararon las 5 re-ingestas con `--force`
(2022 secuencial primero, luego 2023/2024/2025/2026 en paralelo -- sin
`concurrency:` group en el workflow, sin colision de datos porque cada
escritura esta scoped por `season` y `game_pk` es unico globalmente en la
MLB Stats API). Las 5 completaron sin errores y con `validate-ingestion`
en `status="ok"` (100% cobertura de snapshot en las 5, cobertura de
campos rolling de Trend entre 79%-86% segun temporada -- esperable, los
primeros dias de cada temporada no tienen ventana completa de 7-14 dias).

Al re-correr `jsa_historical_discriminative_audit.yml`/
`jsa_historical_resolution_audit.yml` sobre las 5 temporadas re-ingeridas
y comparar contra el baseline congelado (`pre_trend_2026-07-16/`), las
metricas de los 5 pilares que NO deberian haber cambiado
(starter/bullpen/offense/team_quality/context) mostraron un drift real y
no trivial (`loso_brier` -0.000169, `loso_ece` -0.000952, `loso_mce`
-0.084898 a nivel agregado; temporadas 2023/2024/2025 con el MISMO numero
exacto de juegos en ambas corridas mostraron Brier/accuracy distintos por
temporada). Investigacion completa (diff de codigo descartando bug propio,
revision de registries descartando race condition, imposibilidad de
diffear snapshots crudos porque `clear_season()` los borra fisicamente
antes de cada `--force`) documentada en
`jsa/docs/baselines/post_reingest_trend_2026-07-17/README.md`.

**Decision del usuario** (2026-07-17): aceptar la limitacion metodologica
(re-ingerir contra una fuente externa viva puede producir variaciones
pequenas sin cambios de codigo propio; la causa exacta no es demostrable
retrospectivamente porque los snapshots originales no fueron
versionados), fijar `post_reingest_trend_2026-07-17/` como el nuevo
baseline de referencia para el desarrollo de Trend, y dejar como regla
para el futuro: cualquier comparacion que requiera reproducibilidad
EXACTA debe conservar tambien los snapshots crudos (o un artefacto
equivalente), no solo las metricas agregadas del audit. El AUC por pilar
(invariante de escala) confirmo que ninguna decision previa se ve
afectada -- diferencias en el 4to-5to decimal nada mas, y trend/historical
siguen 100% inertes en ambas corridas.

## `historical/trend_candidate_audit.py` -- auditoria descriptiva + LOSO de los 4 candidatos de Trend

Con el nuevo baseline fijado, siguiente paso del orden acordado con el
usuario: auditoria descriptiva de los 8 campos rolling + comparacion LOSO
de los 4 candidatos (OPS 7d/14d, ERA 7d/14d) antes de tocar `trend.py`.

- `run_descriptive_audit(records)`: cobertura, distribucion (mean/std/
  percentiles/extremos) de cada uno de los 8 campos crudos, y correlacion
  cruzada entre ellos (ej. cuanto se solapan las ventanas de 7d y 14d del
  mismo campo).
- `evaluate_trend_candidates(records)`: mismo patron que
  `resolution_audit.py::evaluate_team_quality_alternatives()` -- NUNCA
  reemplaza produccion. Para cada candidato, calcula un diff continuo
  (mismo criterio que `offense_factor()` para OPS, diff directo para ERA,
  ambos ya usados en produccion), lo z-scorea sobre la distribucion real
  (juegos sin ventana completa quedan en z=0, neutral, igual que el
  `advantage=0` que Trend produce hoy para TODOS los juegos), sustituye
  UNICAMENTE el valor de `trend` con el MISMO peso que tiene hoy en
  `BASE_PILLAR_WEIGHTS`, corre LOSO, y compara via bootstrap CI (500
  resamples, igual que el resto de los audits) contra dejar Trend en 0
  (estado real de produccion). Solo un `delta_brier_mean` negativo Y
  `significant=True` justificaria implementar ese candidato en `trend.py`.
- 8 tests nuevos (`test_trend_candidate_audit.py`), incluyendo los 2
  sanity checks anti-fuga ya establecidos como estandar en este proyecto:
  moneda pura sin relacion con la forma reciente inyectada -> AUC~0.5 para
  los 4 candidatos; forma reciente real y persistente inyectada -> al
  menos un candidato con AUC>0.55 y mejora significativa via bootstrap.
- `jsa_historical_trend_candidate_audit.yml` -- mismo patron
  `workflow_dispatch(seasons)` que discriminative/resolution-audit, solo
  lectura, nunca toca `trend.py` ni ningun registry, timeout 30 min.

Explicitamente NO decide todavia si algun candidato se implementa --
eso requiere correr este audit contra datos reales (5 temporadas
re-ingeridas) y revisar el resultado con el usuario antes de escribir una
sola linea en `trend.py`.

## Resultado real de `jsa_historical_trend_candidate_audit.yml` -- linea cerrada, NO adoptada

Corrida real sobre las 5 temporadas (2022-2026, 13,101 juegos, run
[29621086180](https://github.com/joassit/mlb_edge_analyzer.v2/actions/runs/29621086180)).

**Auditoria descriptiva**: cobertura ~85% en los 8 campos (esperable,
inicio de temporada sin ventana completa), distribuciones sanas (OPS
rolling ~0.715 std 0.08-0.10, ERA rolling ~4.13-4.18 std 1.1-1.4, sin
outliers patologicos). 7d vs 14d del mismo campo/equipo correlaciona
~0.76-0.78 (esperable, ventanas solapadas); OPS y ERA del mismo equipo
casi no correlacionan entre si (~-0.02) -- **no hay evidencia de un
problema de ingenieria o calidad de datos** en los 8 campos.

**Comparacion LOSO** (sustituir Trend=0 por cada candidato, z-scoreado,
mismo peso, bootstrap CI vs. el estado real de produccion):

| Candidato | AUC individual | Cobertura | Δ Brier vs. Trend=0 | Significativo |
|---|---|---|---|---|
| `ops_rolling_7d` | 0.533 | 85.1% | -0.000006 | No |
| `ops_rolling_14d` | 0.539 | 85.5% | +0.000104 | No |
| `era_rolling_7d` | 0.529 | 85.1% | +0.000183 | No |
| `era_rolling_14d` | 0.542 | 85.5% | **+0.000340** | **Si** |

**Decision del usuario (2026-07-17): NO implementar Trend con estos
candidatos.** Ningun candidato mejora el Brier de forma significativa; el
unico resultado estadisticamente significativo (`era_rolling_14d`) es un
**deterioro** (CI +0.000142 a +0.000542, enteramente positivo) -- por lo
tanto queda explicitamente descartado en su forma actual, no solo "sin
evidencia de mejora". `trend.py` se mantiene como stub documentado
(`advantage=0` siempre) -- esta es la conclusion correcta del experimento,
no una limitacion pendiente de resolver.

**Que se conserva**: toda la infraestructura (`load_records_with_trend_
candidates()`, `run_descriptive_audit()`, `evaluate_trend_candidates()`,
el workflow, los 8 campos ya persistidos en el schema 3.4) sigue
disponible para evaluar candidatos DISTINTOS sin reconstruir el pipeline
-- el costo marginal de probar una proxima hipotesis de Trend es correr
el mismo audit, no escribir codigo nuevo desde cero.

**Regla para el futuro**: no volver a probar exactamente esta
aproximacion (rolling OPS/ERA de equipo a 7d/14d, mismo tipo de feature)
esperando un resultado distinto -- la pregunta ya fue respondida con
evidencia real. Cualquier proximo candidato de Trend debe ser una senal
de naturaleza distinta (ej. margen de victoria/derrota reciente en vez de
stats acumuladas, racha de W/L, splits home/away recientes, etc.), no una
variacion parametrica de la misma idea.

## Fase 2 -- `historical/historical_candidate_audit.py`: 4 candidatos de historial head-to-head

Siguiente fase del roadmap estrategico del usuario: el pilar `historical`
(Historical Favorite Context) es el otro stub de la Seccion 7.1
(`advantage=0` siempre, ver `engine/pillars/historical.py`). A diferencia
de Trend, esto es **100% derivable offline** de `historical_game` ya
ingerido para las 5 temporadas -- no requiere ninguna re-ingesta ni
golpear la API de MLB.

- `compute_head_to_head_history(engine, seasons)`: point-in-time-safe,
  dia-batched (misma disciplina anti-fuga que `compute_elo_and_
  pythagorean()`: dentro de un `game_date`, todos los juegos primero leen
  el estado pre-dia, recien despues se actualiza el historial con los
  resultados de ese dia), pero **sin resetear entre temporadas** -- a
  diferencia de Elo, un enfrentamiento de 2022 sigue contando para el
  mismo par de equipos en 2023 (verificado con
  `test_head_to_head_history_persists_across_seasons`). Limitacion
  honesta y documentada: "historial" aca significa "desde 2022" (el
  horizonte de datos ingerido), no la rivalidad real completa.
- 4 candidatos calculados por par de equipos especifico: `h2h_win_pct_
  all_time` (% de victorias en todos los enfrentamientos previos dentro
  de la ventana), `h2h_win_pct_last_5` (ultimos 5 enfrentamientos),
  `h2h_run_diff_avg` (diferencia de carreras promedio, no solo victoria/
  derrota), `h2h_recency_weighted` (record ponderado exponencialmente por
  recencia, decay=0.8).
- Mismo patron de evaluacion que Trend: `evaluate_historical_candidates()`
  sustituye UNICAMENTE `historical` (z-scoreado, mismo peso, neutral/0 en
  juegos sin enfrentamiento previo) por cada candidato, LOSO comparado via
  bootstrap CI contra dejar Historical en 0 (estado real de produccion).
- Auditoria descriptiva incluye cobertura (fraccion de juegos con >=1
  enfrentamiento previo -- naturalmente mas baja que Trend, ya que muchos
  pares de equipos se enfrentan pocas veces dentro de un horizonte de 5
  temporadas) y la distribucion de `n_meetings` por juego.
- 9 tests nuevos (`test_historical_candidate_audit.py`), incluyendo los 2
  sanity checks anti-fuga estandar del proyecto y 2 tests especificos de
  integridad point-in-time (el primer enfrentamiento real entre dos
  equipos debe dar `n_meetings=0`; el historial persiste entre
  temporadas).
- `jsa_historical_historical_candidate_audit.yml` -- mismo patron
  `workflow_dispatch(seasons)`, solo lectura, nunca toca `historical.py`
  ni ningun registry, timeout 30 min.

Explicitamente NO decide todavia si algun candidato se implementa --
mismo criterio que Trend: correr esto contra datos reales y revisar el
resultado con el usuario antes de escribir una sola linea en
`historical.py`.

## Resultado real de `jsa_historical_historical_candidate_audit.yml` -- linea cerrada, NO adoptada

Corrida real sobre las 5 temporadas (2022-2026, 13,101 juegos, run
[29625728340](https://github.com/joassit/mlb_edge_analyzer.v2/actions/runs/29625728340)).

**Auditoria descriptiva**: cobertura excelente -- **96.1%** de los juegos
tienen al menos un enfrentamiento previo dentro de la ventana 2022-2026
(vs. 85% en Trend), con un promedio de 20.7 enfrentamientos previos por
juego (mediana 15, hasta 87 en rivalidades de division con mas historial
acumulado). La disponibilidad de datos NO es el problema -- los 4
candidatos presentan distribuciones coherentes, centradas cerca de 0,
sin anomalias de calidad.

**Comparacion LOSO** (sustituir Historical=0 por cada candidato,
z-scoreado, mismo peso, bootstrap CI vs. el estado real de produccion):

| Candidato | AUC individual | Cobertura | Δ Brier vs. Historical=0 | Significativo |
|---|---|---|---|---|
| `h2h_win_pct_all_time` | 0.532 | 96.1% | +0.000051 | No |
| `h2h_win_pct_last_5` | 0.524 | 96.1% | **+0.000348** | **Si** |
| `h2h_run_diff_avg` | 0.539 | 96.1% | +0.000033 | No |
| `h2h_recency_weighted` | 0.525 | 96.1% | +0.000136 | No |

**Decision del usuario (2026-07-18): NO implementar Historical con estos
candidatos.** La capacidad predictiva individual es debil (AUC 0.52-0.54)
y ningun candidato mejora el Brier de forma significativa; el unico
resultado estadisticamente significativo (`h2h_win_pct_last_5`, CI
+0.000143 a +0.000562, enteramente positivo) es un **deterioro** -- por
lo tanto queda explicitamente descartado en su forma actual, no solo
"sin evidencia de mejora". `historical.py` se mantiene como stub
documentado (`advantage=0` siempre) -- esta es la conclusion correcta
del experimento, no una limitacion pendiente de resolver.

**Que se conserva**: toda la infraestructura
(`compute_head_to_head_history()`, `run_descriptive_audit()`,
`evaluate_historical_candidates()`, el workflow) sigue disponible para
evaluar candidatos DISTINTOS sin reconstruir el pipeline.

**Alcance exacto del rechazo**: se descartan especificamente estos 4
candidatos de historial head-to-head (win% all-time, win% ultimos 5,
diferencia de carreras promedio, ponderado por recencia) -- **no el
concepto general de senales historicas**. Cualquier propuesta futura
debe partir de una hipotesis distinta (ej. rendimiento en el mismo
estadio, historial reciente contra el mismo abridor rival, contexto de
"favorito habitual" medido de otra forma) y someterse de nuevo a
validacion LOSO completa antes de implementarse -- no se asume que
"historial head-to-head en general no aporta" a partir de este resultado.

## Trend e Historical -- ambas lineas cerradas en su formulacion actual (2026-07-18)

Con el resultado de arriba, las dos fases del roadmap estrategico que
buscaban activar los pilares stub (`trend`/`historical`) quedan cerradas
con evidencia real: en ambos casos, los candidatos evaluados no superaron
la validacion LOSO -- en ambos casos hubo ademas un candidato
especificamente PEOR de forma estadisticamente significativa
(`era_rolling_14d` para Trend, `h2h_win_pct_last_5` para Historical).
Decision explicita del usuario: priorizar de aqui en adelante mejoras que
ya demostraron aportar valor medible, en vez de seguir agregando
variables cuya contribucion no supera la validacion estadistica. Los dos
pilares siguen activos y reportando (Seccion 7.1: todo pilar debe
evaluarse), transparentes sobre su propia limitacion, tal como estan hoy.

## Diagnostico del techo del modelo (2026-07-18)

Antes de invertir en una fuente de datos nueva (mas costosa e infraestructura
adicional), el usuario pidio un diagnostico explicito: ¿el modelo actual
todavia tiene margen de mejora, o ya esta cerca del limite alcanzable con
el enfoque actual (7 pilares, combinacion lineal ponderada,
calibracion isotonica)? Todo lo que sigue proviene de la auditoria real
sobre **13,101 juegos (5 temporadas, 2022-2026) con validacion LOSO**
(`jsa/docs/baselines/post_reingest_trend_2026-07-17/discriminative_audit_result.json`)
-- no son observaciones anecdoticas.

**Alcance explicito de estas conclusiones**: todo lo que sigue describe
el techo alcanzable **con el espacio de informacion evaluado hasta
ahora** -- los 7 pilares actuales (starter/bullpen/offense/team_quality/
context/trend/historical), sus insumos concretos (ERA/OPS de temporada
con shrinkage, clima/lesiones, rolling OPS/ERA, historial head-to-head),
y una combinacion lineal ponderada de esos pilares. **No es una
afirmacion sobre el techo teorico de predecir resultados de MLB en
general.** Una fuente de informacion genuinamente distinta (Statcast,
lineups confirmados, cuotas de mercado, u otra arquitectura de modelo no
lineal) podria mover este techo -- de hecho es exactamente lo que la
Fase de Statcast busca poner a prueba. Esta seccion se re-evalua cada vez
que el espacio de informacion evaluado cambia de forma material (una
fuente nueva se integra o se descarta con evidencia), no es una
conclusion fija de una vez para siempre.

**1. Modelo completo vs. cada pilar individual**: el modelo combinado
calibra excelente (`loso_ece=0.00203`, practicamente perfecto) pero
discrimina poco (`loso_brier=0.24506`, cerca del piso de p=0.5
constante). AUC individual por pilar: starter=0.546, bullpen=0.552,
offense=0.547, team_quality=0.532, context=0.500. Cohen's d entre
ganadores/perdedores=0.26 (efecto "pequeño"), coeficiente de solapamiento
de distribuciones=0.90 (90% de superposicion entre la distribucion de
`evidence_score_raw` de partidos ganados vs. perdidos).

**2. Ablacion (Fase 3, LOSO + bootstrap CI)**: starter/bullpen/offense/
team_quality son **imprescindibles** (remover cualquiera empeora el
Brier de forma estadisticamente significativa, delta +0.0007 a
+0.0014). context/trend/historical son **neutros** (removerlos no
cambia el Brier de forma significativa) -- consistente con context
teniendo AUC=0.500 y trend/historical con advantage=0 siempre.

**3. Redundancia entre pilares core**: correlacion de Pearson moderada
entre starter-bullpen (0.38, ambos son "calidad de pitcheo") y algo
entre bullpen-offense (0.23); el resto <0.12. Ninguna cercana a
colinealidad severa (>0.7-0.8) -- cada pilar aporta informacion
mayormente independiente, no hay duplicacion disfrazada de 4 pilares
distintos.

**4. ¿Falta informacion o falta capacidad de combinarla? -- CONCLUSION
CLAVE**: la optimizacion de pesos nested (Fase 4, sin sesgo de
seleccion, `optimize_weights_nested()`) mostro que re-optimizar
`BASE_PILLAR_WEIGHTS` **no generaliza** (`generalizes: false`,
delta_brier +0.000283, significativo -- los pesos re-optimizados por
fold externo empeoran fuera de muestra respecto a los pesos de
produccion actuales). Esto significa que el modelo lineal actual ya esta
cerca del optimo alcanzable dado el conjunto de informacion existente --
**el cuello de botella es la informacion disponible, no la capacidad del
modelo de combinarla**. Cada pilar individual tiene AUC debil porque las
variables subyacentes (ERA/OPS de temporada con shrinkage) tienen un
techo de senal bajo para predecir un partido individual, no porque la
combinacion lineal este mal ajustada.

**5. Expectativa de mejora al incorporar nuevas fuentes**: juicio
informado por literatura de sabermetria (no medido, marcado
explicitamente como tal) -- un partido individual de MLB tiene un techo
de prediccion bajo por diseno (alta varianza intrinseca del deporte);
modelos publicos conocidos basados en features pre-partido (sin cuotas
de mercado) suelen reportar AUC ~0.58-0.63, no mucho mas alto que los
pilares fuertes actuales (0.53-0.55). Cualquier fuente nueva deberia
evaluarse esperando una mejora incremental (del mismo orden de magnitud
que la ablacion de los pilares core, Brier ~0.0005-0.001), no un salto
transformador.

**Trend e Historical, en el contexto de este diagnostico**: ambas lineas
de investigacion (rolling OPS/ERA 7d/14d para Trend, historial
head-to-head para Historical) se cerraron por falta de evidencia de
mejora bajo el mismo protocolo LOSO + bootstrap CI usado aca -- ningun
candidato de ninguna de las dos lineas supero la validacion, y en ambos
casos hubo un candidato especificamente peor de forma significativa. Ver
las secciones dedicadas arriba para el detalle completo.

**Decision del usuario**: con este diagnostico documentado, la siguiente
prioridad es evaluar Statcast como fuente de datos nueva -- pero
exigiendo el mismo estandar de evidencia (LOSO + bootstrap CI) antes de
integrar cualquier cosa al modelo. Ver
`jsa/docs/statcast_integration_design.md` para el documento de diseno
tecnico previo a escribir codigo.

## Statcast Etapa 1 -- spike de factibilidad, resultado real (2026-07-18)

Corrida real de `jsa_statcast_feasibility_spike.yml` (run
[29637498610](https://github.com/joassit/mlb_edge_analyzer.v2/actions/runs/29637498610)),
sin tocar el modelo ni ninguna base de JSA:

- **Acceso real**: confirmado -- los 3 endpoints candidatos respondieron
  200 OK sin errores de red/proxy. El endpoint de busqueda a nivel evento
  (`statcast_search/csv`) trae exactamente los campos necesarios para
  H1-H4 (`game_date`, `launch_speed`, `estimated_woba_using_speedangle`,
  `inning_topbot`, `game_pk`, `batter`, `pitcher`), 119 columnas en
  total, 1,072 filas para 1 equipo x 1 semana en 9.15s.
- **Cobertura historica**: confirmada para las 5 temporadas (2022-2026),
  incluyendo la temporada en curso.
- **Tiempos/limites**: sin headers de rate-limit detectados; ~9s por
  ventana de 1 equipo x 1 semana -- el principal costo a vigilar.
- **Integridad point-in-time**: el chequeo estricto marco
  `leakage_detected=True`, pero la causa real es una semantica de
  parametro (`game_date_gt`/`game_date_lt` son AMBOS inclusivos, no
  `>`/`<` estrictos como el nombre sugiere) -- no una fuga temporal
  inherente de la fuente. Manejable con el mismo patron ya usado en
  `_end_date()` de `point_in_time_provider.py` (nunca pedirle a la fuente
  un recorte point-in-time directamente; solo usarla para traer HECHOS
  crudos historicos, y hacer la reconstruccion point-in-time-safe
  despues, en Python, exactamente como ya hacen
  Elo/Pythagorean/head-to-head desde `historical_game`).

**Decision del usuario**: Etapa 1 satisfactoria, autoriza avanzar a la
Etapa 2 con alcance deliberadamente limitado -- ingesta minima orientada
exclusivamente a validar H1-H4, sin construir infraestructura general.

## Statcast Etapa 2 -- ingesta minima + comparacion LOSO de H1-H4 (construido, no corrido todavia)

Arquitectura deliberada: **bulk-pull liga completa por ventana de fecha**
(nunca por equipo) hacia una tabla nueva y aislada
`historical_statcast_event` (SOLO `launch_speed` +
`estimated_woba_using_speedangle` por evento de bateo -- ningun otro
campo Statcast) -- mismo patron que `historical_game` (se ingiere el
hecho crudo una vez; toda la reconstruccion point-in-time-safe pasa
DESPUES, en Python, dia por dia, nunca pidiendole a la fuente un recorte
directo). Esto es deliberadamente mas barato que replicar el patron de
Trend (llamada HTTP por equipo por juego), aprovechando que una sola
consulta sin filtro de equipo trae toda la liga para esa ventana.

- `jsa/historical/statcast_ingestion.py`: `ingest_statcast_season_minimal()`
  -- chunkea la temporada en ventanas de 30 dias, reporta EXPLICITAMENTE
  el costo real (tiempo total, bytes de respuesta, eventos por chunk,
  chunks con error) en el resumen devuelto, tal como exigio el usuario
  para poder comparar costo vs. beneficio predictivo.
- `jsa/historical/statcast_candidate_audit.py`: `compute_statcast_candidates()`
  point-in-time-safe, dia-batched, **reseteado por temporada** (mismo
  criterio que `offense`/`starter`/`bullpen`, no acumula entre
  temporadas como head-to-head). Atribuye cada evento a equipo que
  batea/equipo que lanza via `inning_topbot`, y a abridor especifico via
  `historical_game.home_pitcher_id`/`away_pitcher_id` ya ingerido (sin
  necesitar ningun mapeo nuevo de abreviatura de equipo -- se evito ese
  riesgo usando `game_pk` como llave de union, ya confiable).
  - H1: xwOBA de equipo (ofensiva) acumulado en la temporada -- candidato
    de `offense`.
  - H2: xwOBA permitido acumulado del ABRIDOR de ese juego especifico --
    candidato de `starter`.
  - H3: xwOBA permitido acumulado del BULLPEN de equipo -- candidato de
    `bullpen`.
  - H4: hard-hit rate de equipo (ofensiva) rolling 7d/14d -- candidato de
    Trend (comparado contra Trend=0, no contra `offense`).
  - `evaluate_statcast_candidates()`: mismo patron LOSO + bootstrap CI de
    500 resamples que Trend/Historical, pero H1-H3 comparan contra el
    valor REAL de produccion del pilar correspondiente (no contra 0,
    porque starter/bullpen/offense ya son imprescindibles -- barra mas
    alta que Trend/Historical).
- 15 tests nuevos (`test_statcast_ingestion.py`: 8 casos incluyendo
  parseo CSV filtrado a `type=='X'`, chunks de fecha no solapados,
  manejo de errores de red, resumibilidad/`force`;
  `test_statcast_candidate_audit.py`: 7 casos incluyendo el sanity check
  point-in-time de "primer juego de la temporada sin historial todavia"
  y los 2 sanity checks anti-fuga estandar del proyecto).
- `jsa_statcast_minimal_ingest.yml` (ingesta por temporada, timeout 180
  min) + `jsa_statcast_candidate_audit.yml` (solo lectura, timeout 30
  min) -- ninguno de los dos se disparo todavia, queda para hacerlo con
  confirmacion explicita del usuario.

No se decide todavia si algun candidato se implementa -- eso requiere
correr la ingesta real (midiendo el costo real, no solo el estimado del
spike) y el audit LOSO contra datos reales, revisando el resultado con
el usuario antes de escribir una sola linea en `offense.py`/`starter.py`/
`bullpen.py`/`trend.py`.

## Resultado real de la ingesta minima + `jsa_statcast_candidate_audit.yml` -- linea cerrada, NO adoptada

**Costo real de la ingesta** (5 corridas de `jsa_statcast_minimal_ingest.yml`,
en paralelo, 0 errores en 45 chunks): 142,515 eventos de bateo reales
almacenados, 550.5 MB descargados en total, ~48 minutos de computo de
GitHub Actions repartidos en 5 jobs paralelos (tiempo de reloj real
~15 min, la corrida mas lenta). Costo trivial comparado con las horas por
temporada que tomo la re-ingesta de Trend.

**Comparacion LOSO** (run
[29664006135](https://github.com/joassit/mlb_edge_analyzer.v2/actions/runs/29664006135),
13,101 juegos, criterio de exito de la Seccion 7 del diseno tecnico:
`delta_brier_mean` negativo Y `significant=True` Y `|delta_brier_mean|
>= 0.001` -- las 3 condiciones a la vez):

| Hipotesis | Pilar objetivo | AUC | Cobertura | Δ Brier vs. actual | Significativo | Cumple los 3 criterios |
|---|---|---|---|---|---|---|
| `h1_offense_xwoba` | offense | 0.517 | 84.3% | **+0.001240** | Si | **No** |
| `h2_starter_xwoba_allowed` | starter | 0.501 | 59.4% | **+0.001233** | Si | **No** |
| `h3_bullpen_xwoba_allowed` | bullpen | 0.501 | 84.2% | **+0.001523** | Si | **No** |
| `h4_hard_hit_rolling_7d` | trend | 0.515 | 35.5% | +0.000066 | No | No |
| `h4_hard_hit_rolling_14d` | trend | 0.510 | 54.5% | +0.000001 | No | No |

**Decision del usuario (2026-07-18): NO implementar ninguna de las 4
hipotesis.** Este resultado es mas contundente que Trend/Historical: H1,
H2 y H3 muestran un **deterioro estadisticamente significativo** (los 3
CI quedan enteramente del lado positivo) al sustituir offense/starter/
bullpen por sus versiones basadas en xwOBA -- no es solo "no mejora", es
"empeora con confianza estadistica". H4 (candidatos de Trend, rolling
hard-hit rate) no muestra efecto en ninguna direccion.

**Caveat de cobertura, documentado pero NO usado para reabrir la
linea sin nueva evidencia**: H2 (abridor especifico) tuvo solo 59.4% de
cobertura -- bastante menor que H1/H3 (~84%), porque acumular una
muestra de bateos-en-juego permitidos por ESE abridor especifico tarda
mas en la temporada que agregarlo a nivel de equipo. Eso diluye la
comparacion en ~40% de los juegos con z=0 neutral, y probablemente
contribuye al deterioro observado en H2 independientemente de si xwOBA
en si es mejor o peor señal. Consistente con el diagnostico del techo
del modelo: esta implementacion especifica de xwOBA (calculada solo de
bateos-en-juego -- eventos con `type=='X'` -- sin walks/strikeouts que
ERA/OPS si capturan) resulto ser una señal MAS POBRE, no mas rica, que
la que ya se usa.

**Que se conserva**: toda la infraestructura
(`historical_statcast_event`, `statcast_ingestion.py`,
`statcast_candidate_audit.py`, los 2 workflows) sigue disponible para
evaluar candidatos DISTINTOS sin reconstruir el pipeline de ingesta --
en particular, una version de xwOBA que incluya TODOS los resultados de
turno al bate (no solo bateos en juego) resolveria el problema de
cobertura/completitud identificado arriba, y es una hipotesis
legitimamente distinta (no una repeticion parametrica) si se decide
retomar esta linea en el futuro.

**Alcance exacto del rechazo**: se descartan especificamente estas 4
hipotesis (xwOBA de equipo/abridor/bullpen calculado desde bateos en
juego solamente, y hard-hit rate rolling) -- no el concepto general de
metricas Statcast ni la arquitectura de ingesta minima construida.

## `team_quality`: Elo y Pythagorean Expectation bajo el criterio formal de 3 condiciones -- linea cerrada (2026-07-19)

El usuario propuso revisar un lote de metricas nuevas (posicion en tabla/
W-L, carreras generadas/permitidas, HR, hits, LOB, capitalizacion de
carreras por hit, fortaleza de abridor/bullpen/closer, limitar a 5
entradas). Antes de construir nada nuevo, se verifico que dos de esas
ideas -- posicion en tabla/W-L y carreras generadas/permitidas -- ya
tenian una implementacion offline construida hace varias sesiones
(`resolution_audit.py::compute_elo_and_pythagorean()`, point-in-time-safe,
100% calculable desde `historical_game` ya ingerido, sin tocar la API):
Elo (equivalente a W-L, `elo_k=20`, reinicio por temporada) y Pythagorean
Expectation (equivalente a carreras generadas/permitidas, exponente 1.83).
Esos resultados ya existian con LOSO + bootstrap CI, pero nunca se
habian evaluado contra el criterio formal de 3 condiciones (Seccion 7 de
`jsa/docs/statcast_integration_design.md`) que se establecio recien con
la linea de Statcast. Se aplico ese criterio retroactivamente a los
resultados reales ya calculados sobre el dataset actual mas grande
(`jsa/docs/baselines/post_reingest_trend_2026-07-17/resolution_audit_result.json`,
13,101 juegos, 2022-2026) -- sin recalcular nada, sin nueva ingesta.

| Alternativa | Pilar objetivo | AUC | Δ Brier vs. `team_quality` actual | Significativo | \|Δ\| >= 0.001 | Cumple los 3 criterios |
|---|---|---|---|---|---|---|
| Elo (`elo_diff`) | team_quality | 0.559 | **+0.000460** | Si (CI: [0.0000397, 0.000867]) | No | **No** |
| Pythagorean Expectation | team_quality | 0.559 | **+0.000479** | Si (CI: [0.0000130, 0.000914]) | No | **No** |

**Ambas alternativas fallan en 2 de las 3 condiciones a la vez**: el
delta de Brier es positivo (serian PEORES si sustituyeran a
`team_quality`, no mejores) Y estadisticamente significativo (el CI de
bootstrap queda enteramente del lado positivo) Y el tamaño de efecto
(~0.00046-0.00048) queda de todas formas por debajo del minimo de
`0.001` aunque el signo hubiera sido favorable. Con un dataset mas chico
(baseline `pre_trend_2026-07-16`, 13,099 juegos) Elo habia dado
`significant=False` -- con mas datos reales (post re-ingesta de Trend) el
resultado se volvio inequivoco: ambas alternativas son significativamente
peores que la implementacion actual de `team_quality` (lesiones + closer
disponible + fielding_pct).

**Decision: linea cerrada, no se adopta ninguna.** Confirma con evidencia
formal lo que ya sugeria el resultado preliminar: agregar record de
temporada o diferencial de carreras como sustituto de `team_quality` no
mejora el modelo bajo el mismo protocolo que Trend/Historical/Statcast.

**Que se conserva**: `resolution_audit.py::compute_elo_and_pythagorean()`
y `evaluate_team_quality_alternatives()` siguen disponibles para evaluar
una alternativa genuinamente distinta a `team_quality` en el futuro (por
ejemplo, Elo con regresion entre temporadas, o un `pyth_exponent`
recalibrado) sin reconstruir el pipeline -- pero seria una hipotesis
nueva, no una repeticion de esta.

**Alcance exacto del rechazo**: se descarta especificamente reemplazar
`team_quality` por Elo o por Pythagorean Expectation tal como estan
definidos hoy -- no se descarta la idea de que W-L/run-differential
puedan aportar algo en una formulacion distinta (por ejemplo, como señal
adicional combinada con lo que ya usa `team_quality`, en vez de un
reemplazo total).

## Cuatro lineas cerradas (Trend, Historical, Statcast H1-H4, Elo/Pythagorean) -- estado consolidado (2026-07-19)

Con este resultado, las cuatro fases de "agregar informacion nueva" del
roadmap estrategico terminaron sin evidencia de mejora bajo el mismo
protocolo LOSO + bootstrap CI + criterio de tamaño de efecto minimo. En
tres de los cuatro casos hubo ademas al menos un candidato especificamente
PEOR de forma estadisticamente significativa (Trend: `era_rolling_14d`;
Historical: `h2h_win_pct_last_5`; Statcast: H1, H2 y H3 los tres;
Elo/Pythagorean: las dos alternativas completas). El diagnostico del
techo del modelo (ver seccion dedicada arriba) sigue siendo la lectura
vigente: el cuello de botella no esta en como se combinan los pilares
(la optimizacion de pesos ya esta cerca del optimo), sino en el techo de
informacion de las señales concretas ya probadas -- pero esto aplica
estrictamente al espacio de informacion evaluado hasta ahora, no es una
afirmacion general sobre el techo teorico de predecir MLB (ver la nota
de alcance explicita en el diagnostico).

## Game Flow Engine v1.0 -- Etapa 1 (construido, no corrido todavia, 2026-07-19)

Auditoria previa (2026-07-19, mismo dia): se confirmo que JSA no modela
el desarrollo temporal del partido -- evalua un `GameSnapshot` estatico
pre-partido, sin conceptos de inning, transicion al bullpen, situacion de
salvamento ni estado esperado del juego. El usuario propuso un Game Flow
Engine completo (Starter/Bullpen/Closer Projection, dominancia por fases,
pesos dinamicos, Win State Projection); tras verificar variable por
variable contra el codigo real, la mayoria no tiene dato hoy (matchup vs.
lineup, pitch count real, dias de descanso, forma reciente -- ya
evaluada y rechazada como Trend --, xFIP/WHIP/K%/BB% del cerrador) o
requiere boxscore/linescore no ingerido (HR/hits/LOB/entradas). El
usuario re-escopo la propuesta a una Etapa 1 deliberadamente limitada:
solo lo construible con datos YA persistidos, como modulo de generacion
de candidatos (mismo rol que Trend/Historical/Statcast/Elo-Pythagorean),
nunca wireado a `engine/pillars/` ni a `BASE_PILLAR_WEIGHTS` sin
evidencia. Ver `jsa/docs/game_flow_design.md` para el diseno completo.

**2 hipotesis evaluadas** (`jsa/historical/game_flow_candidate_audit.py`):
- `gf1_starter_durability`: sustituye el insumo de `starter` (hoy ERA con
  shrinkage) por un diff de "probabilidad de completar >=6 entradas",
  derivado de `home/away_starter_projected_ip` (el mismo proxy que ya usa
  `context_detector.py` para `long_outing`/`short_outing_bullpen_game`),
  modelado como Normal(mu=projected_ip, sigma=1.2 heuristico). Prueba si
  la durabilidad esperada predice el resultado distinto a la calidad
  (ERA) del abridor.
- `gf2_bullpen_dependency`: sustituye el insumo de `bullpen` (hoy ERA con
  shrinkage + penalizacion de closer) por ese mismo diff escalado por
  cuanto se espera que dependa el partido del bullpen
  (`expected_bullpen_ip = 9 - projected_ip` de cada equipo). Prueba si la
  ventaja de bullpen importa mas en partidos bullpen-dependientes.

**Sin ninguna ingesta nueva**: ambas hipotesis se derivan enteramente de
campos que ya estan en `historical_snapshot` desde la ingesta original --
a diferencia de Statcast, este workflow no depende de correr una ingesta
previa.

**Limitacion honesta documentada** (ver diseno, Seccion 3): no existe en
ningun lado del proyecto un registro de cuantas entradas lanzo
efectivamente un abridor en un juego historico especifico
(`historical_game` solo persiste el resultado final) -- por lo tanto
`sigma=1.2` es un heuristico sin calibrar contra ese ground truth (mismo
criterio de honestidad que `SHRINKAGE_K_IP`/`OFFENSE_FACTOR_EXPONENT` en
`config.py`). GF1/GF2 se validan igual que Elo/Pythagorean/Statcast:
sustituyendo el diff en el pilar objetivo y midiendo si mejora la
prediccion real de `home_win` via LOSO -- no calibrando su propia
probabilidad interna contra IP real por juego (ese dato no existe; seria
obtenible via `stats=gameLog` por pitcher, mucho mas barato que el
boxscore completo de Fase 2, pero NO autorizado en esta entrega).

**Deliberadamente fuera de esta Etapa 1**: Closer Rating (separar el ERA
del cerrador del resto del bullpen -- requiere un campo nuevo en
`historical_snapshot` y su propia re-ingesta de 5 temporadas, no
autorizada todavia); dominancia por fases, Win State Projection, pesos
dinamicos, First 5 Innings (requieren boxscore/linescore real, ninguno
ingerido).

**Mismo criterio de 3 condiciones que Statcast** (Seccion 5 del diseno):
significancia + `|delta_brier_mean| >= 0.001` + costo justificado por el
usuario -- ninguna adopcion automatica.

Implementacion: `jsa/historical/game_flow_candidate_audit.py` (2
hipotesis, LOSO + bootstrap CI), CLI `game-flow-candidate-audit`,
workflow `.github/workflows/jsa_game_flow_candidate_audit.yml`
(`workflow_dispatch`, solo lectura), 8 tests en
`jsa/tests/test_game_flow_candidate_audit.py` (formas, monotonicidad,
sanity checks anti-fuga -- coinflip puro y recuperacion de senal
inyectada --, punta a punta). Suite completa de `jsa/` verificada:
314 passed, 3 skipped tras el agregado.

## Resultado real de `jsa_game_flow_candidate_audit.yml` -- linea cerrada, NO adoptada

Corrida real (run
[29669963835](https://github.com/joassit/mlb_edge_analyzer.v2/actions/runs/29669963835),
13,101 juegos, 5 temporadas 2022-2026, ~50 segundos -- solo lectura,
sin ingesta, commit base `6d430f9` tras el merge de PR #34). Mismo
criterio de 3 condiciones que Statcast (Seccion 5 del diseno tecnico):
`delta_brier_mean` negativo Y `significant=True` Y `|delta_brier_mean|
>= 0.001` -- las 3 a la vez:

| Hipotesis | Pilar objetivo | AUC | Cobertura | Δ Brier vs. actual | Significativo | \|Δ\|>=0.001 | Cumple los 3 criterios |
|---|---|---|---|---|---|---|---|
| `gf1_starter_durability` | starter | 0.522 | 83.5% | **+0.000911** | Si | No | **No** |
| `gf2_bullpen_dependency` | bullpen | 0.561 | 86.1% | **+0.000391** | Si | No | **No** |

**Decision (2026-07-19): NO implementar ninguna de las 2 hipotesis.**
Mismo patron que Elo/Pythagorean y Statcast H1-H3: ambas alternativas
son **significativamente PEORES** que los insumos actuales de
`starter`/`bullpen` (los 2 CI de bootstrap quedan enteramente del lado
positivo -- deterioro, no mejora) Y ninguna alcanza el tamaño de efecto
minimo de `0.001` de todas formas (GF1 se acerca mas, `0.000911`, pero
sigue por debajo Y en la direccion equivocada).

**Lectura**: la reformulacion de "calidad del abridor" como
"probabilidad de completar 6 entradas" (GF1) y la ponderacion de
"ventaja de bullpen" por dependencia esperada (GF2) no capturan
informacion nueva que ERA/bullpen-ERA ya no capturen -- son
transformaciones de las MISMAS variables subyacentes (`projected_ip`,
`bullpen_era`), no una fuente de informacion distinta. Consistente con
el diagnostico del techo del modelo: recombinar/reponderar señales ya
usadas rara vez mueve el Brier; solo lo hizo agregar informacion
genuinamente nueva, y en las 3 fuentes probadas hasta ahora (Trend,
Historical, Statcast) tampoco funciono.

**Que se conserva**: `game_flow_candidate_audit.py` sigue disponible
para evaluar hipotesis futuras derivadas de un ground truth real de IP
por juego (via `stats=gameLog`, ver limitacion de diseno Seccion 3) o de
boxscore/linescore (Closer Rating, dominancia por fases) -- ninguna de
esas dos vias esta autorizada ni construida todavia.

**Alcance exacto del rechazo**: se descartan especificamente estas 2
transformaciones de `projected_ip`/`bullpen_era` (Normal con
`sigma=1.2` sin calibrar, dependencia lineal de bullpen) -- no el
concepto general de modelar durabilidad/dependencia de bullpen, si en
el futuro se dispone de ground truth real de IP por juego.

## Cinco lineas cerradas (Trend, Historical, Statcast H1-H4, Elo/Pythagorean, Game Flow GF1-GF2) -- estado consolidado (2026-07-19)

Las 5 lineas de "agregar informacion nueva o recombinar la existente"
evaluadas hasta ahora bajo el mismo protocolo LOSO + bootstrap CI +
tamaño de efecto minimo terminaron sin evidencia de mejora. En 4 de las
5 hubo ademas al menos un candidato especificamente PEOR de forma
significativa (Trend: `era_rolling_14d`; Historical:
`h2h_win_pct_last_5`; Statcast: H1, H2 y H3; Elo/Pythagorean: ambas;
Game Flow: ambas). El diagnostico del techo del modelo sigue siendo la
lectura vigente, con su alcance explicito: aplica al espacio de
informacion evaluado hasta ahora (7 pilares, sus insumos concretos, y
ahora tambien sus recombinaciones/reponderaciones), no es una
afirmacion sobre el techo teorico de predecir MLB en general. Las
unicas vias no cerradas requieren datos genuinamente nuevos (boxscore/
linescore, xwOBA con walks/strikeouts incluidos, IP real por juego via
`gameLog`) -- ninguna construida ni autorizada todavia.

## `cross_model` -- puente de resultados entre JSA, Game Flow y el modelo legado (2026-07-19)

El usuario pidio "una base de datos de la cual podamos correr distintos
modelos [JSA, MLB legado, Game Flow]" -- precisado a: poder **cruzar
resultados con SQL directo** entre los 3 sistemas (comparar precision,
no solo compartir infraestructura). No hizo falta un proyecto nuevo: los
3 sistemas ya coexisten en este repositorio, cada uno con su propia base
deliberadamente aislada (`DATABASE_URL`/`HISTORICAL_DATABASE_URL` legado,
`JSA_DATABASE_URL`/`JSA_HISTORICAL_DATABASE_URL` JSA). `game_pk` es
`Integer` en los 3 -- confirmado por investigacion directa del codigo
legado (`db/database.py`, `historical_engine/db.py`), sin friccion para
un join.

**Decision de diseño (confirmada por el usuario)**: un sync/ETL de solo
lectura, nunca instrumentar `persist_run()` (JSA) ni `save_picks()`/
`save_analysis()` (legado) -- cero riesgo sobre los pipelines de
produccion existentes, reversible, suficiente para analisis/comparacion.

**Construido** (`cross_model/`, paquete nuevo en la raiz del repo, fuera
de `jsa/` y del codigo legado para no comprometer el aislamiento de
ninguno de los dos):
- `cross_model/db.py`: tabla `unified_model_predictions` (`game_pk,
  game_date, season, system, model_name, model_version, raw_score,
  home_win_prob, predicted_winner, actual_winner, correct, source_ref`,
  unique en `(game_pk, system, model_name, model_version)`) +
  `upsert_prediction()` (upsert real, nunca duplica) +
  `accuracy_by_system_and_model()` (el ejemplo concreto de "cruzar con
  SQL directo": accuracy por sistema+modelo en una sola consulta).
- `cross_model/sync_jsa.py`: sincroniza `evidence_score_raw` (JSA
  historico, real, 5 temporadas ya ingeridas) y GF1/GF2 (Game Flow) desde
  `jsa/historical/db.py` -- CLI `python -m cross_model.sync_jsa
  --jsa-historical-db ... --season ...`.
- `jsa/storage/dialect_utils.py::upsert()`: helper nuevo, dialect-aware
  (Postgres/SQLite), agregado al modulo YA compartido entre todos los
  motores de storage de JSA -- reusado por `cross_model/db.py` sin
  duplicar la logica de `ON CONFLICT DO UPDATE`.
- 9 tests nuevos (`cross_model/tests/`, su propio `pytest.ini`/
  `conftest.py` -- corre standalone con `pytest cross_model/`): schema,
  upsert idempotente, sync end-to-end contra una base JSA sembrada
  sinteticamente, y la demostracion explicita de cruzar JSA vs. Game Flow
  para los mismos juegos con una sola consulta SQL. Suite completa de
  `jsa/` reverificada tras el cambio a `dialect_utils.py`: 314 passed, 3
  skipped. `tests/test_historical_isolation.py` (legado) tambien
  reverificado: 6 passed -- `cross_model` no toca ninguna tabla de
  produccion de ninguno de los 2 sistemas.

**Extension (2026-07-19, misma sesion): sync del modelo legado autorizado
explicitamente por el usuario ("Utiliza el secret")**. Se agrego
`cross_model/sync_legacy.py`: sincroniza picks `moneyline` reales
(`db.database.Pick`/`ActualResult`) -- con su PROPIO engine/sesion
construido desde la URL recibida, nunca `db.database.SessionLocal`
(evita quedar atado al `DATABASE_URL` que existiera al importar el
modulo). A diferencia de JSA/Game Flow, `home_win_prob` del legado SI se
llena con un numero real: `Pick.model_prob` es la probabilidad que ese
sistema ya usa para apostar dinero real en produccion, normalizada a
"probabilidad de que gane home". Probado con una base de produccion
sintetica (4 tests nuevos, incluyendo uno que hashea `picks`/
`actual_results` antes y despues del sync para confirmar cero escritura
-- mismo criterio que `tests/test_historical_isolation.py`); 13 tests
totales en `cross_model/tests/`, todos passing.

Se agrego `.github/workflows/cross_model_sync.yml` (`workflow_dispatch`,
on-demand): corre los 3 syncs usando `secrets.DATABASE_URL` (el MISMO
secret que ya usa `daily_pipeline.yml` para el legado en produccion) y
`secrets.JSA_HISTORICAL_DATABASE_URL` (como fuente de JSA/Game Flow Y
como destino de `unified_model_predictions` -- misma instancia de
Postgres ya verificada real en corridas anteriores de esta sesion), mas
un paso final que imprime `accuracy_by_system_and_model()` como artifact.

**Resuelto (2026-07-19, misma sesion): `secrets.DATABASE_URL` paso de no
existir a apuntar a un Neon Postgres real.** El legado nunca habia tenido
un Postgres externo configurado -- `daily_pipeline.yml` corria contra el
fallback de `actions/cache` (best-effort, `mlb_edge.db` SQLite entre
corridas). El usuario decidio migrar a Neon para no arriesgar perder el
historico. Bug real encontrado y corregido en el camino: la migracion
(`scripts/migrate_sqlite_to_postgres.py`) fallo primero por falta de
`psycopg2-binary` en `requirements.txt` (PR #42), y despues con
`IntegrityError: NotNullViolation` en `picks.calibration_phase`/`forced`
-- causa real: `db/database.py::_auto_add_missing_columns()` agrega
columnas nuevas via `ALTER TABLE ADD COLUMN` crudo sin `NOT NULL`, asi
que filas viejas de SQLite tienen `NULL` real ahi aunque el modelo
declare `nullable=False`; Postgres si lo exige. Arreglado rellenando esas
columnas con su default declarado antes de insertar (PR #43, con test
que reproduce el historial real de `ALTER TABLE` en vez de
`create_all()`, que si habria impedido sembrar el NULL en el test).

**Migracion real completada** (`migrate_legacy_to_postgres.yml`, run
[29702267751](https://github.com/joassit/mlb_edge_analyzer.v2/actions/runs/29702267751),
tras el merge de PR #43): `game_analysis`: 144 filas, `actual_results`:
127, `feature_snapshots`: 144, `picks`: 134, `bets`: 0 -- sin errores.

**`cross_model_sync.yml` corrido contra el Neon real** (run
[29702495152](https://github.com/joassit/mlb_edge_analyzer.v2/actions/runs/29702495152),
`sync_legacy=true` usando ya `secrets.DATABASE_URL` real -- no el
fallback de cache -- `sync_jsa_gameflow=false` para evitar el
re-sync lento ya documentado arriba): `sync_legacy_moneyline_picks`
sincronizo `n_picks=134`. `accuracy_by_system_and_model()` final con los
3 sistemas:

| Sistema | Modelo | Version | Juegos | Aciertos | Accuracy |
|---|---|---|---|---|---|
| jsa | evidence_score_raw | jsa-v3.0-historical-backtest | 11,277 | 6,192 | 54.91% |
| game_flow | gf1_starter_durability | game_flow_v1_etapa1 | 10,778 | 5,583 | 51.80% |
| game_flow | gf2_bullpen_dependency | game_flow_v1_etapa1 | 11,280 | 6,140 | 54.43% |
| mlb_legacy | legacy_skellam | 0.5.0-reconectado | 75 | 40 | 53.33% |
| mlb_legacy | legacy_skellam | 0.6.0-skellam-calibrado | 39 | 16 | 41.03% |
| mlb_legacy | legacy_unknown | 0.5.0-reconectado | 2 | 1 | 50.00% |

La muestra del legado (116 picks) es chica frente a JSA/Game Flow
(~11k juegos cada uno) porque solo cubre lo que el pipeline diario de
produccion alcanzo a generar hasta ahora -- no es una limitacion del
sync, es cuantos picks reales existen todavia.

**Objetivo original cumplido**: los 3 sistemas (JSA, Game Flow, legado)
coexisten en el mismo Postgres real (`unified_model_predictions`) y se
pueden cruzar con SQL directo, sin instrumentar ningun pipeline de
produccion existente. `daily_pipeline.yml` ya esta preparado para usar
`secrets.DATABASE_URL` automaticamente en su proxima corrida (via
`HAS_EXTERNAL_DB`) sin cambio de codigo adicional.

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
- **Ajuste + validacion YA CONSTRUIDO** (ver seccion "Calibracion
  isotonica de `evidence_score_raw`" arriba): `historical/calibration.py`
  ajusta y valida (leave-one-season-out) una curva isotonica real sobre
  las 5 temporadas, persistida en `calibration_registry`. **Lo que falta
  todavia**: wirear `orchestrator.py` para que LEA una entrada
  `status="validated"` de `calibration_registry` y recien ahi pase
  `JSAReport.calibration.calibration_status` de `"uncalibrated"` a
  `"calibrated"` -- deliberadamente diferido a una entrega separada, con
  su propia revision explicita (el Confidence Gate empieza a poder pasar
  de verdad, un cambio de comportamiento demasiado grande para mezclar
  con la construccion del ajuste). Reliability diagrams (graficos, no
  solo los numeros de ECE/MCE ya calculados) tambien quedan para esa
  entrega o una posterior.
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

## Fase 3 -- Significancia estadistica formal + Experiment Registry poblado (2026-07-20)

Primera pieza de infraestructura (no de "agregar informacion nueva", ya
agotada en las 5 lineas cerradas de arriba) construida desde el
diagnostico del techo del modelo: Seccion 12.8 exigia bootstrap/McNemar/
permutacion antes de graduar nada de `experimental` a `active` -- hasta
ahora solo existia el bootstrap pareado (duplicado en 5 modulos
distintos), y `experiment_registry` seguia vacio pese a que 5
investigaciones reales ya habian corrido bajo el mismo protocolo.

**`jsa/historical/significance.py`** (nuevo, unico lugar de esta logica
de ahora en adelante): `paired_bootstrap_ci()` (el mismo bootstrap
pareado de siempre, extraido de `discriminative_audit.py` -- los 5
modulos que lo usaban ahora importan de aca, cero cambio de
comportamiento, 53 tests de candidate audits pre-existentes reverificados
sin modificar). Se agregan las 2 pruebas que faltaban:
- `mcnemar_test()`: pareado sobre aciertos/errores binarios (`p>=0.5==y`,
  mismo criterio que `accuracy()`), correccion de continuidad, chi2 1 gl.
- `permutation_test_delta_brier()`: sign-flip pareado sobre el delta de
  Brier -- decide de nuevo con probabilidad 0.5 cual prediccion es
  "baseline"/"alt" DENTRO de cada juego, preserva la estructura pareada.
- `full_significance_report()`: combina las 3 (alpha=0.10 consistente en
  las 3, mismo nivel que el CI de bootstrap al 90% ya usado desde
  Statcast) + el tamaño de efecto minimo (`|Δ|>=0.001`) -- `passes_all_
  three=True` unicamente si TODAS coinciden en mejora real. Bar mas
  estricto que cualquier candidate audit anterior (que solo exigia
  bootstrap + tamaño de efecto), reservado para decidir promocion real de
  `experimental` a `active` -- nunca para reportar un resultado cualquiera.

**`jsa/historical/rule_candidate_audit.py`** (nuevo): primera evaluacion
formal de las 6 reglas heredadas (`engine/rule_definitions.py`) contra el
historico real -- ninguna tenia todavia un experimento de respaldo
(`engine/rule_engine.py` las evalua y traza desde el dia 1, pero
`applied_to_weights` es `False` siempre porque `status="experimental"`
para las 6). Verificado contra `snapshot_reconstruction.py` cuales
triggers tienen dato real: 5 de 6 (`long_outing`,
`short_outing_bullpen_game`, `key_offensive_injuries`, `double_header`,
`extreme_travel`). `bullpen_fatigue` queda excluida -- su campo
(`home/away_bullpen_ip_last_3_days`) esta declarado en `domain/models.py`
pero nunca se llena en ningun lado de la ingesta (siempre `None`);
testearla ahora seria fingir evidencia sobre un trigger que nunca puede
disparar.

Mecanismo: para cada regla y cada juego, reconstruye un `GameSnapshot`
real del payload persistido (`GameSnapshot(**payload)`, mismo patron que
`validation.py::benchmark_season()`) y llama a `context_detector.py::
detect_context()` sin modificarlo -- el trigger evaluado es EXACTAMENTE
el de produccion, nunca una reimplementacion paralela. Donde el trigger
dispara, recalcula el score con los pesos que resultarian de aplicar la
regla en solitario (`engine/weight_engine.py::apply_weights()`, reusado
tal cual); donde no dispara, el score no cambia. Compara via LOSO +
`full_significance_report()` contra `evidence_score_raw` real (pesos
base, ninguna regla aplicada -- el estado real de produccion hoy).

**`jsa/historical/experiment_backfill.py`** (nuevo): formaliza las 5
lineas ya cerradas (Trend, Historical, Statcast, Elo/Pythagorean, Game
Flow) como filas reales de `experiment_registry`, citando los numeros YA
obtenidos (documentados arriba en este mismo archivo) y sus run_id de
GitHub Actions donde aplica -- nunca recalcula nada. Idempotente (mismo
criterio que `registries/seed.py`).

**CLI** (`jsa/historical/cli.py`): `rule-candidate-audit --db ... --season
... [--sync-to-registries --registries-db ...]` -- sin el flag, solo
reporta (igual que cualquier candidate audit anterior); con el flag,
ADEMAS escribe un `experiment_registry` por regla (`decision=
"promoted_active"` o `"rejected"`) y, si `passes_all_three=True`, agrega
una fila nueva a `rule_registry` con `status="active"` y
`experiments_supporting_rule=[experiment_id]` real -- la primera vez que
esto pasa en el proyecto (append-only, nunca sobreescribe la fila
`experimental` anterior). `backfill-closed-experiments
[--registries-db ...]` para el paso anterior.

**Workflows nuevos**: `jsa_rule_candidate_audit.yml`
(`workflow_dispatch(seasons, sync_to_registries=false por default)` --
promover una regla a produccion real requiere decidirlo explicitamente en
cada dispatch, nunca automatico) y `jsa_backfill_closed_experiments.yml`
(sin inputs, idempotente).

**Tests**: `test_significance.py` (11, incluyendo predicciones identicas
-> nada significativo, alternativa claramente mejor/peor -> las 3
pruebas de acuerdo en la direccion correcta) + `test_rule_candidate_
audit.py` (10, incluyendo sanity check anti-fuga -- coinflip puro sin
senal inyectada -> ninguna regla pasa las 3 pruebas -- y recuperacion de
señal real inyectada especificamente en el subconjunto de juegos
disparados, usando el `weight_adjustments` real de la regla como
generador). 21 tests nuevos, mas los 53 de candidate audits
pre-existentes reverificados tras la extraccion de `paired_bootstrap_ci`.

**Pendiente antes de correr contra Postgres real**: disparar
`jsa_backfill_closed_experiments.yml` primero (puebla las 5 lineas
cerradas), despues `jsa_rule_candidate_audit.yml` con
`sync_to_registries=false` para revisar el resultado real de las 5
reglas, y solo con confirmacion explicita del usuario re-disparar con
`sync_to_registries=true` si alguna merece promocion.

## Resultado real de Fase 3 contra Postgres real (2026-07-20) -- ninguna regla promovida

`jsa_backfill_closed_experiments.yml` corrio primero (run
[29714734860](https://github.com/joassit/mlb_edge_analyzer.v2/actions/runs/29714734860)):
10 filas insertadas en `experiment_registry` (las 5 lineas cerradas de
arriba, cada Statcast H1-H4 como filas separadas). Despues
`jsa_rule_candidate_audit.yml` corrio en modo solo-reporte (run
[29714773930](https://github.com/joassit/mlb_edge_analyzer.v2/actions/runs/29714773930),
13,101 juegos reales, 5 temporadas):

| Regla | Dispara | Δ Brier | Bootstrap | McNemar | Permutacion | Mejora | Pasa las 3 |
|---|---|---|---|---|---|---|---|
| `long_outing` | 1,136 (8.7%) | -0.0000054 | No | No | No | Si (ruido) | **No** |
| `short_outing_bullpen_game` | 1,778 (13.6%) | +0.000131 | No | No | No | No | **No** |
| `key_offensive_injuries` | 8,566 (65.4%) | +0.000166 | No | No | No | No | **No** |
| `double_header` | 236 (1.8%) | +0.0000090 | No | No | No | No | **No** |
| `extreme_travel` | 299 (2.3%) | -0.0000286 | No | No | No | Si (ruido) | **No** |

Deltas ordenes de magnitud por debajo del umbral minimo (`0.001`) y sin
significancia en ninguna de las 3 pruebas -- a diferencia de Elo/
Pythagorean (peor CON significancia), este es un resultado limpio de
"sin efecto detectable". Reponderar pesos entre pilares para estos
triggers especificos no mejora el modelo -- mismo diagnostico del techo
del modelo que todas las lineas anteriores.

**Decision (2026-07-20): ninguna regla se promueve.** Las 6 reglas
heredadas quedan `experimental` (`bullpen_fatigue` sigue sin dato real
para evaluarse). Se re-disparo `jsa_rule_candidate_audit.yml` con
`sync_to_registries=true` (run 29714945418) para formalizar los 5
resultados como filas `decision="rejected"` en `experiment_registry` --
15 experimentos reales en total (10 backfill + 5 reglas), ninguna
promocion a `rule_registry.status="active"`. **Fase 3 cerrada.**

## Fase 4 -- Calibracion isotonica wireada a produccion (2026-07-20)

El ajuste isotonico de `evidence_score_raw` ya estaba construido y
validado (LOSO, 5 temporadas reales) desde la entrega anterior, pero
`engine/orchestrator.py` nunca leia `calibration_registry` -- `calibration_
status` quedaba `"uncalibrated"` siempre, sin importar si existia una
curva validada. Esta entrega cierra ese vacio.

**Hallazgo antes de escribir codigo** (cambio el alcance real de esta
Fase): `engine/confidence_gate.py` dice explicitamente que el Gate nunca
pasa por DOS razones independientes (Seccion 8.4.1 + 10.4) -- pero el
codigo de `evaluate_gate()` solo chequeaba la primera
(`calibration_status`). Wirear SOLO la calibracion habria dejado que el
Gate empezara a pasar de verdad sin que su propio Gate Threshold Sweep
(Fase 6, todavia no existe) lo respalde -- una promocion prematura,
exactamente lo que el proyecto evita en todo lo demas. Se corrigieron
las 2 piezas juntas, no solo la pedida originalmente.

**Cambios**:
- `engine/orchestrator.py::_build_calibration_info()` (nuevo): lee
  `calibration_registry_rows[config.PRODUCTION_CALIBRATION_ID]` (nuevo
  constante en `config.py`, mismo valor que el default de
  `historical/cli.py calibrate --calibration-id`). Si existe y
  `status=="validated"`, aplica la curva (`x_knots`/`y_knots`) sobre
  `evidence_score_raw` via interpolacion lineal con clip en los bordes
  (`np.interp`, reproduce exactamente `IsotonicRegression(out_of_bounds=
  "clip").predict()` sin necesitar sklearn en el camino de produccion).
  `raw_probability` pasa a derivarse de `evidence_score_raw` -- reemplaza
  el valor Skellam-derivado que se usaba antes (Projected Runs, Seccion
  9, sigue existiendo como modulo separado, solo dejo de alimentar
  `CalibrationInfo`).
- `engine/confidence_gate.py::evaluate_gate()`: nuevo parametro
  `gate_registry_rows` -- corta con `reason="gate_not_validated"` si
  `gate_registry_rows.get(f"gate-{market_id}-v1", {}).get("status") !=
  "validated_70"`, ANTES de evaluar los criterios reales. Docstring del
  modulo reescrito para dejar las 2 condiciones explicitas en el codigo,
  no solo en el comentario.
- `jsa/main.py` y `jsa/historical/pipeline.py`: ambos leen
  `calibration_registry_rows`/`gate_registry_rows` (mismo patron
  `registries_db.latest_by_id()` que ya usan para rule/feature/pillar
  registry) y los pasan a `evaluate_game()` -- produccion en vivo Y el
  motor historico comparten la MISMA logica, sin excepcion (principio
  del proyecto desde la primera entrega).

**Resultado esperado real, verificado con tests**: con una curva
validada en `calibration_registry` pero SIN ningun `gate_registry`
`validated_70` (el estado real hoy -- los 4 sembrados en
`"under_validation"`), `calibration_status` pasa a `"calibrated"` y
`final_category` deja de ser siempre `"NO_DISPONIBLE_SIN_CALIBRAR"` --
pero el Confidence Gate sigue sin pasar, ahora con
`reason="gate_not_validated"` en vez de `"uncalibrated"`. Comportamiento
correcto, no un bug: falta Fase 6 (Gate Threshold Sweep) para que el
Gate pueda pasar de verdad.

**Tests**: `test_calibration_wiring.py` (6, unitarios sobre
`_build_calibration_info()`: sin entrada, entrada no validada, entrada
validada con knots vacios, curva real aplicada correctamente,
clipping fuera de rango, id de calibracion equivocado ignorado) +
`test_confidence_gate.py` extendido (2 tests nuevos: Gate bloqueado por
`gate_not_validated` aun calibrado, entrada de gate_registry ausente) +
`test_pipeline_integration.py` extendido (2 tests nuevos end-to-end:
categoria real una vez que existe una curva validada pero Gate sigue
bloqueado por `gate_not_validated`; con AMBOS registries validados, el
Gate ya no se bloquea por infraestructura, solo por sus criterios
reales). Suite completa de `jsa/` reverificada tras el cambio: sin
regresiones.

**Explicitamente NO en esta entrega**: ningun calibration_registry ni
gate_registry real se pobló en Postgres todavia -- eso se resolvio
despues, ver seccion siguiente.

## Calibracion real activada en produccion (2026-07-20)

Tras mergear Fase 4, se disparo `jsa_historical_calibrate.yml` contra el
Postgres real (5 temporadas, 13,101 juegos):

| Metrica | Valor |
|---|---|
| `loso_seasons_validated` | 2022, 2023, 2024, 2025, 2026 (las 5) |
| `loso_n_games` | 13,101 |
| `loso_brier` | 0.2451 |
| `loso_accuracy` | 55.3% |
| `loso_ece` | 0.0020 |
| `status` | **`validated`** |

`calibration_registry` tiene ahora una fila real `status="validated"`
bajo `calibration-evidence_score_raw-v1` (`config.PRODUCTION_CALIBRATION_ID`).
A partir de la proxima corrida de `daily_pipeline.yml`, `calibration_
status` pasa a `"calibrated"` y `final_category` deja de mostrar siempre
`"NO_DISPONIBLE_SIN_CALIBRAR"` -- categorias reales en produccion por
primera vez. El Confidence Gate sigue bloqueado (`reason=
"gate_not_validated"`) hasta Fase 6.

## Fase 6 -- Gate Threshold Sweep con nested walk-forward (2026-07-20)

**Dos hallazgos antes de escribir codigo, que definieron el alcance
real**: (1) solo `moneyline_home`/`moneyline_away` tienen una
probabilidad calibrada propia -- `evidence_score_raw`/la curva isotonica
solo predicen P(home gana), nunca margen ni total de carreras, asi que
`run_line`/`totals` quedan fuera (gap real, no se les inventa un gate).
(2) Barrer un grid de `(p_min, cri_min, uncertainty_max)` y quedarse con
el mejor combo tiene el mismo riesgo de sesgo de seleccion que
`discriminative_audit.py::optimize_weights()` vs
`optimize_weights_nested()` -- se resolvio con el mismo criterio: nested
walk-forward real, nunca LOSO simple.

**`jsa/historical/gate_threshold_sweep.py`** (nuevo): por cada temporada
externa, el threshold se elige usando SOLO las 4 internas (su propia
curva isotonica, ajustada UNICAMENTE sobre esas 4 -- nunca lee el campo
`calibration` ya persistido en los reportes historicos, que quedo
congelado en `"uncalibrated"` de antes de Fase 4), y se evalua en la
externa. Seleccion del mejor combo por LIMITE INFERIOR de Wilson (nunca
accuracy cruda, para no favorecer un combo con muestra chica y suerte).
`status="validated_70"` solo si el CI Wilson agregado (pooled sobre las
5 corridas externas) tiene limite inferior >=70% Y hay >=3 temporadas
validadas Y >=30 juegos pasando el gate (heuristico de partida, sin
calibrar contra el proyecto -- ver docstring). Thresholds de PRODUCCION
se ajustan sobre TODA la muestra (mismo criterio que `calibration.py`:
la curva de produccion usa todos los datos; el nested walk-forward valida
el PROCEDIMIENTO de seleccion, no literalmente esos thresholds).

**CLI**: `gate-threshold-sweep --db ... --season ... [--sync-to-registries
--registries-db ...]` -- sin el flag, solo reporta; con el flag, escribe
una fila NUEVA por mercado en `gate_registry` (append-only, nunca
sobreescribe la fila `"under_validation"` sembrada) con los thresholds
de produccion y el status real.

**Workflow nuevo**: `jsa_gate_threshold_sweep.yml`
(`workflow_dispatch(seasons, sync_to_registries=false por default)`).

**Tests**: `test_gate_threshold_sweep.py` (10: Wilson CI, inversion de
probabilidad para `moneyline_away`, `MIN_COVERAGE_N` respetado, sanity
check anti-fuga -- coinflip puro nunca alcanza `validated_70` -- y
recuperacion de señal real fuerte -- SI alcanza `validated_70` en ambos
mercados moneyline).

**Resultado real (2026-07-20, corrida 29784136297, `sync_to_registries=false`,
13,101 juegos, 5 temporadas contra Postgres real)**: AMBOS mercados
`rejected_insufficient_data`. No es solo el nested walk-forward el que
rechaza -- el ajuste de PRODUCCION sobre TODA la muestra combinada (sin
ningun split, el caso mas favorable posible) tampoco encontro un solo
combo de los 100 del grid con >=30 juegos pasando
`(probability > p_min AND cri_score >= cri_min AND uncertainty_index <=
uncertainty_max)`, ni en el extremo mas laxo
(`p_min=0.55, cri_min=70, uncertainty_max=50`). `production_thresholds=null`
en ambos casos -- no se escribio nada en `gate_registry` (correcto: no
hay nada que sincronizar).

Los defaults de produccion en vivo (`GATE_P_MIN=0.65, GATE_CRI_MIN=85,
GATE_UNCERTAINTY_MAX=40`, `jsa/config.py`) caen dentro del rango del grid
barrido, asi que el grid en si no es irrazonablemente angosto. La
hipotesis de trabajo es que `cri_score`/`uncertainty_index` calculados
por la reconstruccion historica (`historical/pipeline.py`) resultan
sistematicamente distintos (CRI mas bajo y/o incertidumbre mas alta) que
los que ve produccion en vivo -- point-in-time, sin ciertas fuentes que
si tiene el pipeline diario -- pero esto todavia no esta confirmado con
datos reales, solo es la explicacion mas plausible.

**Diagnostico agregado antes de decidir nada mas** (mismo principio de
"nunca fabricar, siempre medir"): `gate_threshold_sweep.py::
diagnose_gate_inputs()` + CLI `gate-threshold-diagnostic --db ... --season
...` + workflow `jsa_gate_threshold_diagnostic.yml` -- reporta percentiles
reales (p0/p10/p25/p50/p75/p90/p100) de `cri_score`, `uncertainty_index`
y probabilidad calibrada por mercado sobre los juegos ya ingeridos. Nunca
acepta ningun parametro de registries (verificado con un test de firma
dedicado) -- es estructuralmente imposible que este comando escriba en
`gate_registry` por accidente. **Pendiente**: disparar
`jsa_gate_threshold_diagnostic.yml` contra Postgres real para ver la
distribucion real y decidir, con esos numeros en mano, si el grid de
`gate_threshold_sweep.py` necesita ampliarse o si el gap esta en la
fidelidad de la reconstruccion historica frente a produccion en vivo.

**Resultado real del diagnostico (2026-07-20, corrida 29786153380)**:
```
cri_score_percentiles:         p0=0.0  p10=8.0  p25=16.0  p50=26.0  p75=26.0  p90=26.0  p100=26.0
uncertainty_index_percentiles: p0=40.0 p10=40.0 p25=44.0  p50=52.0  p75=60.0  p90=60.0  p100=72.0
market_probability (moneyline_home): p0=0.28 p50=0.53 p90=0.61 p100=0.75
```
`cri_score` nunca supero 26 en los 13,101 juegos -- eso, no el grid, es
la causa completa del rechazo (`cri_min` mas laxo del grid era 70). La
probabilidad calibrada no es el problema (centrada razonablemente cerca
de 0.5, como se espera de una curva bien calibrada).

**Hallazgo critico, mas alla del alcance de Fase 6 -- bug real en
produccion en vivo, corregido 2026-07-20**: `compute_cri()`
(`jsa/engine/evidence_engine.py`) suma como maximo
`18+18+12+12+8+7=75` puntos (`CRI_COMPONENTS` en `jsa/config.py`) --
nunca puede llegar a 100 pese a que el score se clippea a `[0,100]`.
`GATE_CRI_MIN` estaba en **85**, diez puntos POR ENCIMA del techo
matematico -- `cri_above_min` en `confidence_gate.py` era
estructuralmente `False` siempre, en produccion en vivo tambien, no
solo en el backtest historico. Corregido a `GATE_CRI_MIN=70` (mismo
valor que `CRI_THRESHOLD_CLEAR_FAVORITE`, ya usado en
`decision_engine.py`, y alcanzable solo cuando los 6 componentes
positivos estan presentes -- exige dato completo, que es la intencion
original). Test de regresion agregado:
`test_evidence_engine.py::test_cri_max_possible_score_is_75_not_100`
-- verifica el techo real Y que ningun umbral de CRI en `config.py` lo
supere, para que este tipo de bug no pueda reaparecer en silencio.

Aparte, y sin tocar (todavia) el gap de fidelidad historica: el techo
REAL de `cri_score` dentro del backtest (26, no 75) es un limite
legitimo y esperado -- `historical/snapshot_reconstruction.py` fija
`lineups_official=False`, `bullpen_usage_known=False`,
`no_last_minute_changes=False` a proposito, porque esas 3 senales
(confirmacion oficial de lineup, bullpen ya usado, cambios de ultimo
momento) describen informacion del dia del partido que reconstruir
retroactivamente desde box scores no puede replicar de forma fiel. No
es un bug -- es la razon de fondo por la que `CRI_MIN_GRID` de
`gate_threshold_sweep.py` (70-90) nunca tuvo chance de encontrar un
combo valido.

**`CRI_MIN_GRID` reescalado (2026-07-20)**: de `(70, 75, 80, 85, 90)` a
`(0, 8, 16, 18, 26)` -- los 5 niveles nuevos son los valores REALES y
discretos que `compute_cri()` puede producir dado el techo de 26 del
backtest (`starters_confirmed` + combinaciones de `xera_available`/
`missing_projected_ip`, ver docstring de `gate_threshold_sweep.py`), no
numeros arbitrarios. `UNCERTAINTY_MAX_GRID` (20-50) se deja igual --
el rango observado (40-72) SI se solapa con el grid actual (40 y 50 son
utiles), a diferencia del caso de CRI donde el solapamiento era cero.

**Nota metodologica pendiente, no resuelta por el rescalado**: un
`cri_min` que "valida" contra el techo bajo del backtest (26) puede
resultar trivialmente laxo si se usa tal cual en produccion en vivo
(techo 75, con lineups/bullpen/last-minute-changes reales disponibles
ese mismo dia) -- el PROCEDIMIENTO de nested walk-forward sigue siendo
valido cientificamente, pero el THRESHOLD numerico resultante hereda el
techo bajo del dato historico y no deberia asumirse directamente
aplicable a produccion en vivo sin una comparacion cri_score
historico-vs-en-vivo aparte.

## Resultado real del re-run tras las 2 correcciones (2026-07-21, corrida 29795822229)

Con `GATE_CRI_MIN=70` y `CRI_MIN_GRID` reescalado, el Gate Threshold
Sweep encuentra por primera vez datos que pasan el gate en ambos
mercados -- pero ninguno llega a `validated_70`:

| Mercado | seasons_validated | n_games_passing_gate | accuracy | wilson_ci_low | wilson_ci_high | coverage_pct | thresholds | status |
|---|---|---|---|---|---|---|---|---|
| moneyline_home | las 5 | 473 | 63.0% | 0.5856 | 0.6723 | 3.61% | p_min=0.60, cri_min=16, uncertainty_max=50 | validated_below_70 |
| moneyline_away | las 5 | 66 | 71.2% | 0.5936 | 0.8073 | 0.50% | p_min=0.65, cri_min=0, uncertainty_max=50 | validated_below_70 |

Sincronizado a `gate_registry` con `sync_to_registries=true` (corrida
29796873325) -- documentacion tecnica real, nunca promocion: `confidence_
gate.py` solo desbloquea un mercado con `status=="validated_70"` exacto,
asi que `validated_below_70` queda registrado sin afectar produccion en
vivo.

## Game Flow Research Lab (2026-07-21)

Con el resultado real de arriba como piso, el usuario decidio perseguir
el 70% subiendo la capacidad predictiva real del modelo (nuevas hipotesis
validadas) en vez de seguir moviendo thresholds -- `jsa/research_lab/`
(ver su propio `README.md` para la metodologia completa) es el entorno
de investigacion incremental para eso.

**Principios acordados**: JSA de produccion queda completamente estable
(el laboratorio nunca cambia comportamiento en vivo por si solo --
`test_production_isolation.py` extendido para exigirlo en CI); el
resultado de arriba (`validated_below_70`) es el BASELINE del
laboratorio, nunca el objetivo final; cada hipotesis nueva responde una
unica pregunta -- *¿aporta informacion adicional al baseline?* -- con un
reporte obligatorio de 9 metricas minimas (`HypothesisReport` en
`research_lab/hypothesis_report.py`: delta accuracy/ROC-AUC/Brier/Log
Loss/ECE/ROI/Lift por Edge/Cobertura del Gate + feature importance); una
hipotesis se queda en el laboratorio aunque no llegue a 70% si demuestra
mejora ESTADISTICAMENTE CONSISTENTE (bootstrap pareado de
`significance.py`, CI que no cruza 0, misma alpha=0.10 de siempre) sobre
el baseline en 1+ metrica; ninguna hipotesis se integra a produccion sin
pasar el Scientific Validation Pipeline completo (misma regla dura de
siempre, ver abajo).

**Estado real de cada metrica del reporte obligatorio** (nunca fabricado
-- ver tabla completa en `research_lab/README.md`): accuracy/brier/log
loss/ECE ya tienen fuente real (`historical/calibration.py`); ROC-AUC y
lift por decil (proxy de "lift por edge") ya tienen fuente real
(`historical/discriminative_audit.py::performance_curves()`, construida
en una fase anterior); feature importance ya tiene fuente real
(`evidence_engine.py::compute_feature_contribution()` -- matematicamente
identica a SHAP para el Evidence Score actual, que es lineal). **`delta_
roi` es un gap real**: JSA no tiene ninguna cuota de mercado (moneyline
odds/vig) ingerida en su base historica todavia. El proyecto legado
(`model/edge.py`: `implied_prob`/`fair_odds`/`expected_value`/`no_vig_
probs`) ya tiene una convencion real y validada para esto -- se porta a
`jsa/legacy/` (mismo patron que el heuristico ERA/OPS) el dia que se
ingieran cuotas historicas reales, nunca antes con datos inventados.

**Modulos priorizados** (orden sugerido por el usuario, cada uno
activable/desactivable de forma independiente para medir contribucion
marginal real): Closer Leverage Engine, Team Strength Engine, Offensive
Flow Engine, Starter Projection avanzado, Bullpen Projection avanzado,
Win State Projection, First 5 Research Model. Antes de construir cada
uno: confirmar que existe dato real que soporte la hipotesis en lo ya
ingerido (mismo principio que `rule_candidate_audit.py` en Fase 3).

**Pendiente**: investigar viabilidad de datos reales para el primer
modulo (Closer Leverage Engine) antes de construirlo.

## Closer Leverage Engine -- Modulo 1 del laboratorio (2026-07-21)

**Investigacion de datos real, antes de escribir codigo**: `closer_
pitcher_id` (relevista con mas saves point-in-time del roster) y `home/
away_closer_available` (binario, disponible/lesionado) ya existen y estan
wireados en produccion (`engine/pillars/bullpen.py`, penalty fijo de 0.30
runs-equivalentes). `pitcher_recent_ip_as_of()` -- point-in-time-safe, ya
real y probado, usado hoy en `historical/injuries.py` -- permite calcular
IP reciente del cerrador sin ninguna ingesta nueva. Gap real: `closer_
pitcher_id` no se persistio durante la ingesta original (`GameSnapshot`
solo guarda el booleano derivado), asi que recalcularlo requiere
re-pedir roster + stats por pitcher de bullpen -- mismo costo real de red
que `bullpen_era_as_of()` durante la ingesta original.

**Codigo completo, pendiente de correr contra datos reales**:
- `jsa/historical/db.py`: nueva tabla `historical_closer_leverage`
  (idempotente por `game_pk`+`team_id`).
- `jsa/research_lab/hypotheses/closer_leverage/backfill.py`: re-deriva
  `closer_pitcher_id` + IP reciente por equipo por juego.
- `jsa/research_lab/hypotheses/closer_leverage/evaluate.py`: recalcula el
  advantage de `bullpen` con un penalty de fatiga adicional (grid
  `(0.05, 0.10, 0.15, 0.20)` runs-equivalentes por IP reciente, acotado al
  mismo techo que "cerrador lesionado"), LOSO + `full_significance_
  report()` contra el baseline real -- mismo patron ya aceptado
  (`discriminative_audit.py::shrinkage_sensitivity()`), nunca llama a
  `bullpen.evaluate()` con un snapshot sintetico.
- `jsa/research_lab/cli.py` (nuevo, separado de `historical/cli.py` a
  proposito): `closer-leverage-backfill` / `closer-leverage-evaluate`.
- `.github/workflows/jsa_closer_leverage_backfill.yml` (una temporada por
  corrida, timeout 340 min -- **costo real de red**, volumen comparable a
  una fraccion significativa de la ingesta historica original de esa
  temporada) y `jsa_closer_leverage_evaluate.yml` (nunca pide red).
- 10 tests nuevos (`test_closer_leverage.py`), FakeProvider determinista,
  nunca red real en CI.

**Pendiente, requiere confirmacion explicita antes de cada dispatch**:
correr `jsa_closer_leverage_backfill.yml` para UNA temporada primero
(validar el resultado real antes de comprometerse a las 5 completas),
despues `jsa_closer_leverage_evaluate.yml` con `sync_to_lab_registry=
false` para ver si la hipotesis mejora el baseline, y solo con
confirmacion explicita adicional `sync_to_lab_registry=true` para
documentarlo en `experiment_registry`.

## Regla dura para todo lo anterior

Ninguna de estas piezas se agrega editando directamente un registry o un
umbral a mano. Cada una entra por el Scientific Validation Pipeline
completo (Seccion 13): experimento registrado, benchmarking obligatorio,
prueba de significancia, y veredicto de Quality Gates -- igual que exige
el spec para cualquier extension futura (Principio 16).
