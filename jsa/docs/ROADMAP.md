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

## Explicitamente NO construido todavia (y por que)

Estas piezas requieren historial de produccion acumulado para tener
sentido real -- construirlas ahora, sin datos, significaria fingir una
validacion que el propio spec prohibe declarar sin evidencia (Seccion
10.4: n>=50 juegos/temporada, walk-forward de >=3 temporadas; Seccion
13.6: ventanas moviles mensuales).

### Fase 3 — Experimentacion y Benchmarking (Secciones 12, 13)
- Experiment Engine real corriendo experimentos (tabla `experiment_registry`
  ya existe, vacia).
- Benchmarking obligatorio contra reglas ingenuas (12.3).
- Significancia estadistica -- bootstrap, McNemar, permutation test (12.8).
- Sin esto, ninguna regla puede graduar de `experimental` a `active`
  (ver `engine/rule_engine.py`) -- es el primer paso real pendiente.

### Fase 4 — Calibracion y validacion de varianza (Secciones 8.4.1, 9.2)
- Calibracion isotonica con leave-one-season-out + reliability diagrams.
  Mientras no exista, `JSAReport.calibration.calibration_status` se
  mantiene en `"uncalibrated"` y el Confidence Gate nunca pasa -- por
  diseno, no por bug (ver `engine/confidence_gate.py`,
  `engine/decision_engine.py`).
- Validacion de desviacion estandar del margen proyectado vs. la real
  (`ProjectedRunsOutput.variance_validated` se mantiene en `False`).

### Fase 5 — Validacion Cientifica Completa (Secciones 13.1-13.4, 13.7bis)
- Backtesting historico y Walk-Forward Validation.
- Home Bias Audit (13.3), Calibration Audit (13.4).
- Monte Carlo Audit ampliado (13.7bis) -- `JSAReport.monte_carlo_summary`
  se mantiene en `None`.

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
