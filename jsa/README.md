# JSA v3.0 — Joassit System Analytics

Plataforma de investigacion reproducible y auditable para analisis de
partidos de MLB, hermana de [`mlb_edge_analyzer.v2`](../README.md) en este
mismo repositorio, construida a partir de la especificacion "JSA v3.0 —
Especificación Maestra Unificada" y de las lecciones operativas reales de
ese proyecto (ver `docs/ROADMAP.md` para el detalle completo de que se
construyo y que no).

> Usando solo informacion disponible antes del primer lanzamiento, ¿que
> equipo presenta evidencia objetiva mas fuerte para ganar? ¿Y esa
> evidencia es lo bastante fuerte, confiable y verificable como para
> actuar sobre ella?

JSA no es un generador de picks: evalua el 100% de los juegos del dia
(7 pilares, Evidence Score, CRI, Uncertainty Index, todo con auditoria
matematica programatica) y solo declara un pick "accionable" cuando pasa
el Confidence Gate de un mercado especifico -- lo cual, honestamente, no
sucede todavia en esta entrega: el modelo no tiene calibracion validada
(ver mas abajo).

## Que hace HOY, en una frase

Corre en GitHub Actions una vez al dia, evalua cada juego de MLB en
`Preview` con los 7 pilares del spec, genera un `JSAReport` v3 completo
(con Manifest firmado, hashes verificables y Provenance Graph) y lo
persiste -- pero **la categoria de decision y el Confidence Gate quedan
bloqueados a proposito** hasta que exista una calibracion real (Seccion
8.4.1 del spec). Esto no es una limitacion oculta: `JSAReport.
calibration.calibration_status` dice `"uncalibrated"` en cada reporte, y
`JSAReport.final_category` dice literalmente
`"NO_DISPONIBLE_SIN_CALIBRAR"`.

## Arquitectura (Seccion 2 del spec)

```
Data Ingestion (data_sources/) -- MLB Stats API, Open-Meteo, park factors
        |
Feature Store (storage/) -- GameSnapshot inmutable, con snapshot_hash
        |
Context Detector (engine/context_detector.py) -- solo hechos
        |
Rule Engine + Weight Engine (engine/rule_engine.py, weight_engine.py)
        |
7 Pilares (engine/pillars/) -- Advantage +2..-2 por pilar
        |
Evidence Engine (engine/evidence_engine.py) -- Evidence Score, CRI,
        Uncertainty Index, Dominance Detector
        |
Modulo de Carreras Proyectadas (engine/projected_runs.py) -- senal de
        consistencia, no un octavo pilar
        |
Confidence Gate (engine/confidence_gate.py) -- 7 criterios, por mercado
        |
Decision Engine (engine/decision_engine.py) -- Final Category
        |
Report Generator (reporting/report_builder.py) -- JSAReport v3
        |
Provenance Graph (governance/provenance.py) -- nodo firmado append-only

Transversal a todo lo anterior: Manifest + Reglas de Invalidacion
(governance/manifest.py) -- ninguna corrida sin manifest valido cuenta
para nada (Principio 14, sin excepciones).
```

`engine/orchestrator.py::evaluate_game()` es el **unico punto de
evaluacion real** -- una funcion pura de un `GameSnapshot` ya congelado
mas el estado de los registries, sin I/O. Es la funcion que
`jsa/main.py` llama en vivo, y la misma que `jsa/historical/pipeline.py`
reusa sin modificarla para reevaluar juegos de 2022-2026 -- exactamente la
leccion de `model/predictor.py` en el proyecto hermano, ya confirmada en
la practica.

## Motor historico + Monte Carlo (`jsa/historical/`)

JSA tiene autonomia tecnica completa frente a `mlb_edge_analyzer.v2`: no
importa nada de ese proyecto, y es JSA quien controla su propio
backtesting historico y sus propias simulaciones. `jsa/historical/`
reconstruye `GameSnapshot`s punto-en-el-tiempo (nunca `stats=season`,
siempre `stats=byDateRange` con corte estricto antes del juego) para
temporadas ya jugadas y los evalua con la misma funcion pura de
produccion.

```bash
# Ingerir una temporada completa (horas de duracion real -- ver el
# workflow de GitHub Actions, no correrlo localmente salvo para pruebas
# chicas)
python -m jsa.historical.cli season 2022

# Benchmarking (Seccion 12.3): JSA vs. baselines ingenuos vs. modelos legado
python -c "from jsa.historical.validation import benchmark_season; \
  from jsa.historical.config import HISTORICAL_DATABASE_URL; \
  print(benchmark_season(2022, HISTORICAL_DATABASE_URL))"

# Monte Carlo Audit (Seccion 13.7bis): sensibilidad de pesos, nunca predice juegos
python -c "from jsa.historical.monte_carlo import run_monte_carlo_audit; \
  from jsa.historical.config import HISTORICAL_DATABASE_URL; \
  print(run_monte_carlo_audit(2022, HISTORICAL_DATABASE_URL, n_simulations=200))"
```

Base de datos propia (`JSA_HISTORICAL_DATABASE_URL`, default
`sqlite:///jsa_historical.db`), completamente separada de `JSA_DATABASE_URL`
(produccion) -- solo lee los Registries de produccion (metadata
compartida, nunca datos de juego). Ver `.github/workflows/
jsa_historical_ingest.yml` para correr una temporada real (2022-2026)
via `workflow_dispatch`.

## Modelos legado (`jsa/legacy/`)

Los modelos ya calibrados de `mlb_edge_analyzer.v2` (heuristico ERA/OPS,
Skellam, Binomial Negativo NB2, con sus constantes recalibradas contra 4
temporadas reales) se preservan como **rama secundaria de benchmarking**
-- nunca el motor primario. Se usan desde `jsa/historical/validation.py`
para responder la pregunta de la Seccion 12.3: ¿el Evidence Score de JSA
supera a estos modelos ya calibrados? `jsa/main.py` y `jsa/engine/` tienen
prohibido importar de aqui (`tests/test_production_isolation.py` lo
verifica en CI). Ver `jsa/legacy/README.md` para la procedencia exacta de
cada constante.

## Instalacion y uso

```bash
cd jsa
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Correr el analisis de hoy
python -m jsa.main

# Correr los tests (puros, sin red)
pytest -q
```

Variables de entorno relevantes (todas opcionales, con default sensato):

```bash
export JSA_SEASON=2026
export JSA_DATABASE_URL="postgresql://usuario:password@host:5432/jsa"  # default: sqlite:///jsa.db
```

No requiere ninguna API key: solo usa MLB Stats API y Open-Meteo, ambas
gratuitas y sin autenticacion. (JSA no integra cuotas de mercado -- ver
`docs/ROADMAP.md` sobre por que eso es una decision deliberada, no una
omision.)

## Los 4 Registries de extensibilidad (Principio 16 del spec)

Agregar una metrica, una regla, un pilar o un mercado nuevo mañana **no
puede** invalidar retroactivamente lo que ya esta corriendo. Los 4
mecanismos que lo garantizan viven en `registries/db.py` como tablas
append-only reales desde el primer commit:

| Registry | Gobierna | Estado en esta entrega |
|---|---|---|
| Feature Registry | metricas individuales | 3 features base, `experimental` |
| Rule Registry | las 6 reglas heredadas de v2.4 | todas `experimental` -- **no mueven pesos en produccion** hasta tener un experimento de respaldo real (Seccion 6.6) |
| Pillar Registry | los 7 pilares + extensiones futuras | los 7 base `active`; mecanismo de pilar `experimental` probado en `tests/test_pillar_extensibility.py` |
| Market Registry | los 4 mercados base + nuevos | 4 mercados `active`; sus Gates arrancan `under_validation` |
| Schema Migration Registry | evolucion de `GameSnapshot` | 1 migracion real ya aplicada: `3.0 -> 3.1` (contexto de liga, aditiva) |

## Gobernanza criptografica (Secciones 14-15)

Cada corrida de cada juego genera un `RunManifest` con `snapshot_hash`,
`config_hash` y `output_hash`, verificados independientemente (no solo
confiados) antes de marcar la corrida como valida. Las 12 reglas de
invalidacion automatica de la Seccion 15 corren sobre **toda** corrida
desde el primer commit -- ver `governance/manifest.py` y
`tests/test_invalidation_rules.py` (una por regla).

## Migrar a Postgres

JSA esta diseñado desde el dia 1 para poder migrar 100% a Postgres --
Git/SQLite no son una solucion adecuada de persistencia a largo plazo
(principio rector de esta entrega). Las tres bases de JSA son
independientes y cada una migra por separado, solo cambiando su variable
de entorno:

| Base | Variable | Default |
|---|---|---|
| Produccion (Registries + Feature/Results Store + Reportes) | `JSA_DATABASE_URL` | `sqlite:///jsa.db` |
| Historica (`jsa/historical/`) | `JSA_HISTORICAL_DATABASE_URL` | `sqlite:///jsa_historical.db` |

Pasos:

1. Crea la base en un proveedor administrado (Neon, Supabase, RDS, etc.)
   y anota el connection string (`postgresql://usuario:password@host:5432/nombre_db`).
2. `psycopg2-binary` ya es parte de `requirements.txt` (no opcional --
   los runners de GitHub Actions no pueden instalar un driver ad hoc a
   mitad de un workflow como si fuera un shell de desarrollo local, y el
   principio rector de esta entrega es que JSA sea Postgres-ready desde
   el dia 1, no "instalable a mano si alguien lo necesita").
3. `export JSA_DATABASE_URL="postgresql+psycopg2://..."` (y/o
   `JSA_HISTORICAL_DATABASE_URL`) y corre `python -m jsa.main` /
   `python -m jsa.historical.cli season <año>` -- las tablas se crean
   solas (`init_storage`/`init_registries`/`init_historical_storage`),
   no hace falta ninguna migracion manual de esquema.
4. En GitHub Actions, configura el mismo valor como secret
   (`JSA_DATABASE_URL`/`JSA_HISTORICAL_DATABASE_URL`) -- los workflows ya
   lo leen y saltan la cache de SQLite automaticamente cuando el secret
   existe.

**Por que esto no es solo una promesa:** `storage/dialect_utils.py::
insert_ignore_duplicates()` es el unico punto donde los tres motores de
storage (`registries`, `storage`, `historical`) resuelven upserts
idempotentes, y es dialect-aware de verdad (SQLite `ON CONFLICT DO
NOTHING` y Postgres `ON CONFLICT DO NOTHING` via
`sqlalchemy.dialects.postgresql.insert`) -- se encontro y corrigio un gap
real en esta misma entrega (`.prefix_with("OR IGNORE", dialect="sqlite")`
no hacia nada en Postgres) y **se verifico contra un Postgres real**, no
solo en teoria (`tests/test_postgres_compat.py`, se salta automaticamente
si `TEST_POSTGRES_URL` no esta configurado).

## GitHub Actions

- `.github/workflows/jsa_tests.yml`: pytest en cada push/PR que toque `jsa/`.
- `.github/workflows/jsa_daily_pipeline.yml`: corrida diaria, con las
  mismas lecciones operativas duras aprendidas en `mlb_edge_analyzer.v2`
  (ver comentarios inline en el workflow): cron principal + respaldo
  desplazado (GitHub `schedule` es best-effort, confirmado con evidencia
  real en el proyecto hermano), guarda de idempotencia, `secrets`
  resuelto a nivel de job (nunca en un `if:` de step), SQLite persistido
  via `actions/cache` con advertencia visible de su naturaleza
  best-effort (o `JSA_DATABASE_URL` para Postgres real), y un step final
  que falla fuerte ante un no-op silencioso pero no ante errores/
  invalidaciones aisladas de un solo juego.
- `.github/workflows/jsa_historical_ingest.yml`: ingesta de una temporada
  (2022-2026) via `workflow_dispatch`, timeout 340 min -- solo a mano,
  nunca por schedule.

Secrets opcionales a configurar en GitHub (Settings → Secrets and
variables → Actions): `JSA_DATABASE_URL` / `JSA_HISTORICAL_DATABASE_URL`
(Postgres, recomendado para produccion continua -- ver "Migrar a
Postgres" arriba y la nota de riesgo de `actions/cache` en el workflow).

## Que falta y por que

Ver `docs/ROADMAP.md` -- lista explicita de lo que requiere mas historial
de produccion YA ingerido (no solo el mecanismo para ingerirlo, que ya
existe) para tener sentido real: significancia estadistica formal
(Seccion 12.8), calibracion isotonica real, Drift Detection, Gate
Threshold Sweep validado, Quality Gates consolidados. Tambien documenta
que la migracion de los ~100 picks historicos reales de
`mlb_edge_analyzer.v2` esta bloqueada por falta de acceso a esos datos
desde este entorno, no por falta de codigo.
