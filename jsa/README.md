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
`jsa/main.py` llama en vivo, y la misma que un futuro motor de backtest
(Fase 3-5, ver ROADMAP) deberia reusar sin modificarla -- exactamente la
leccion de `model/predictor.py` en el proyecto hermano.

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

Secrets opcionales a configurar en GitHub (Settings → Secrets and
variables → Actions): `JSA_DATABASE_URL` (Postgres, recomendado para
produccion continua -- ver la nota de riesgo de `actions/cache` en el
workflow).

## Que falta y por que

Ver `docs/ROADMAP.md` -- lista explicita de las Fases 3, 5, 6 y 7 del
spec (Experiment Engine con significancia estadistica, Drift Detection,
Monte Carlo Audit, Gate Threshold Sweep validado, Quality Gates
consolidados) que requieren historial de produccion acumulado para tener
sentido real, y que por eso no se fingen aqui.
