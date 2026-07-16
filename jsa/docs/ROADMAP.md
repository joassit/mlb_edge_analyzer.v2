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

## Regla dura para todo lo anterior

Ninguna de estas piezas se agrega editando directamente un registry o un
umbral a mano. Cada una entra por el Scientific Validation Pipeline
completo (Seccion 13): experimento registrado, benchmarking obligatorio,
prueba de significancia, y veredicto de Quality Gates -- igual que exige
el spec para cualquier extension futura (Principio 16).
