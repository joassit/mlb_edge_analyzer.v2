# jsa/research_lab — Game Flow Research Lab

Entorno de investigacion incremental para perseguir el 70% de accuracy
del Gate (`ACCURACY_VALIDATED_THRESHOLD` en `historical/gate_threshold_
sweep.py`) subiendo la capacidad predictiva real del modelo, en vez de
solo mover thresholds. Acordado con el usuario el 2026-07-21, despues de
que el Gate Threshold Sweep real (Fase 6, PR #46-#49) devolviera
`validated_below_70` para `moneyline_home`/`moneyline_away` con los dos
bugs de configuracion ya corregidos (`GATE_CRI_MIN`, `CRI_MIN_GRID`).

## Regla dura -- igual que `jsa/legacy/`

**Nada en `jsa/main.py`, `jsa/engine/orchestrator.py` ni ningun otro
modulo de produccion en vivo puede importar de `jsa/research_lab/`.**
`tests/test_production_isolation.py` hace cumplir esta regla en CI. El
laboratorio nunca cambia el comportamiento de JSA en produccion por si
solo -- toda promocion real pasa por el mismo Scientific Validation
Pipeline ya establecido (Seccion 13 del spec, ver "Regla dura para todo
lo anterior" en `docs/ROADMAP.md`), nunca por editar un registry a mano.

## Filosofia

No buscamos una variable magica que lleve el modelo al 70% de una vez.
Buscamos que multiples mejoras pequenas, cada una validada con evidencia
estadistica real contra el mismo baseline, se acumulen hasta subir
progresivamente discriminacion, calibracion, ROI y cobertura del Gate.
Hipotesis aisladas, medicion objetiva, comparacion contra baseline,
documentacion completa -- **antes** de cualquier integracion al modelo
principal, nunca despues.

## Baseline (nunca hardcodeado -- `baseline.py` lo lee en vivo)

El baseline es el resultado REAL del Gate Threshold Sweep + la
calibracion de produccion, ya escritos en los registries:

| Metrica | moneyline_home | moneyline_away | Fuente |
|---|---|---|---|
| status | `validated_below_70` | `validated_below_70` | `gate_registry` (gate-moneyline_home-v1 / gate-moneyline_away-v1) |
| accuracy_wilson_ci_low | 0.5856 | 0.5936 | idem |
| accuracy_wilson_ci_high | 0.6723 | 0.8073 | idem |
| coverage_n / coverage_pct | 473 / 3.61% | 66 / 0.50% | idem |
| thresholds (p_min, cri_min, uncertainty_max) | 0.60, 16, 50 | 0.65, 0, 50 | idem |

| Metrica agregada (no por mercado) | Valor | Fuente |
|---|---|---|
| loso_brier | 0.2450574073526572 | `calibration_registry` (calibration-evidence_score_raw-v1) |
| loso_log_loss | 0.6831596807567893 | idem |
| loso_accuracy | 0.5534692008243646 | idem |
| loso_ece | 0.0020263721118798936 | idem |
| loso_mce | 0.05328882810811092 | idem |

Toda hipotesis nueva se compara contra ESTOS numeros, nunca contra una
version anterior de si misma ni contra un numero inventado. `sync_to_
registries=true` en `gate_threshold_sweep`/`calibrate` documenta el
estado real en los registries -- **nunca promueve nada a produccion por
si solo**: `confidence_gate.py` solo desbloquea un mercado con
`status=="validated_70"` exacto, asi que un `validated_below_70` (como
el actual) queda registrado como evidencia tecnica sin afectar el
pipeline en vivo.

## Reporte obligatorio por hipotesis

Toda hipotesis nueva (motor o variable) debe responder una unica
pregunta -- *¿aporta informacion adicional al baseline?* -- y reportarlo
con `HypothesisReport` (`hypothesis_report.py`), que exige como minimo:

| Campo | Fuente real | Estado |
|---|---|---|
| `delta_accuracy` | `historical/calibration.py::loso_fit_and_score()` | listo |
| `delta_brier` | idem | listo |
| `delta_log_loss` | idem | listo |
| `delta_ece` | idem | listo |
| `delta_roc_auc` | `historical/discriminative_audit.py::performance_curves()` | listo |
| `delta_lift_by_edge` | idem (`lift_by_decile`, juegos ordenados por probabilidad/edge del modelo) | listo |
| `feature_importance` | `engine/evidence_engine.py::compute_feature_contribution()` | listo -- en un modelo LINEAL como el Evidence Score actual, SHAP y contribucion-por-peso son matematicamente identicos; si una hipotesis futura introduce un motor no lineal, ahi si hace falta `shap` real, no antes |
| `delta_gate_coverage` | `historical/gate_threshold_sweep.py` (coverage_pct/coverage_n) | listo |
| `delta_roi` | **GAP REAL** | JSA no tiene ninguna cuota de mercado (moneyline odds/vig) ingerida en su base historica todavia. El proyecto legado (`model/edge.py`: `implied_prob`/`fair_odds`/`expected_value`/`no_vig_probs`) ya tiene una convencion real y validada -- se porta a `jsa/legacy/` (mismo patron que el heuristico ERA/OPS) el dia que se ingieran cuotas historicas reales, nunca antes con datos inventados |

## Criterio de permanencia (Seccion 6 del acuerdo)

Una hipotesis se queda en el laboratorio aunque no alcance 70% si
demuestra una mejora **estadisticamente consistente** sobre el baseline
en 1+ metrica relevante. "Estadisticamente consistente" tiene una
definicion precisa, nunca a ojo: el intervalo de bootstrap PAREADO de
`jsa.historical.significance.paired_bootstrap_ci()` sobre el delta de esa
metrica excluye 0 al 90% (misma alpha, misma funcion ya usada en Fase 3 --
nunca reimplementada). `decide_retention()` en `hypothesis_report.py`
aplica esta regla.

## Modulos (orden sugerido, cada uno activable/desactivable independiente)

1. **Closer Leverage Engine** -- codigo completo (2026-07-21), pendiente de backfill real contra Postgres/MLB API
2. Team Strength Engine
3. Offensive Flow Engine
4. Starter Projection avanzado
5. Bullpen Projection avanzado
6. Win State Projection
7. First 5 Research Model

Cada modulo vive en `jsa/research_lab/hypotheses/<modulo>/`, con su
propio `evaluate()` que recibe el baseline real y devuelve un
`HypothesisReport` -- nunca comparte estado con otro modulo, para poder
medir contribucion marginal real de forma aislada. Antes de construir
cada uno: confirmar que existe dato real que soporte la hipotesis en lo
ya ingerido (mismo principio que `rule_candidate_audit.py` en Fase 3 --
nunca fabricar una senal que no esta en los datos).

### Modulo 1 -- Closer Leverage Engine

**Hipotesis**: el estado de fatiga/descanso reciente del cerrador (IP en
los ultimos `days` dias, point-in-time-safe) aporta informacion adicional
al baseline mas alla de la señal binaria actual `home/away_closer_
available` (disponible/lesionado, ya wireada en `engine/pillars/
bullpen.py`).

**Investigacion de datos (2026-07-21)**: `closer_pitcher_id` (el
relevista con mas saves point-in-time del roster) y `home/away_closer_
available` ya existen y estan wireados en produccion. `pitcher_recent_ip_
as_of()` (point-in-time-safe, ya real y probado -- lo usa `historical/
injuries.py`) permite calcular la IP reciente del cerrador SIN ninguna
ingesta nueva de datos. `closer_pitcher_id` en si NO se persistio durante
la ingesta original (`GameSnapshot` solo guarda el booleano derivado) --
recalcularlo requiere re-pedir roster + stats por pitcher de bullpen,
mismo costo real que `bullpen_era_as_of()` durante la ingesta original.

**Codigo** (`hypotheses/closer_leverage/`):
- `backfill.py`: re-deriva `closer_pitcher_id` + IP reciente por equipo
  por juego, persiste en `historical_closer_leverage` (nueva tabla en
  `historical/db.py`, idempotente por `(game_pk, team_id)`).
- `evaluate.py`: recalcula el advantage de `bullpen` con un penalty de
  fatiga adicional (grid `FATIGUE_PENALTY_PER_IP_GRID=(0.05, 0.10, 0.15,
  0.20)`, acotado al mismo techo que "cerrador lesionado" -- fatiga nunca
  penaliza mas que ausencia total), mismo patron ya aceptado en el
  proyecto que `discriminative_audit.py::shrinkage_sensitivity()`. LOSO +
  `full_significance_report()` contra el baseline real por cada valor del
  grid, reporta el mejor.
- CLI: `python -m jsa.research_lab.cli closer-leverage-backfill --db ...
  --season ...` y `closer-leverage-evaluate --db ... --season ...
  [--sync-to-lab-registry --registries-db ...]`.
- Workflows: `jsa_closer_leverage_backfill.yml` (una temporada por
  corrida, **costo real de red** -- volumen comparable a una fraccion
  significativa de la ingesta historica original de esa temporada) y
  `jsa_closer_leverage_evaluate.yml` (nunca pide red, corre sobre lo ya
  backfilleado).

**Pendiente**: correr el backfill real contra Postgres/MLB API -- se
recomienda UNA temporada primero para validar el resultado antes de
disparar las 5 completas, con confirmacion explicita antes de cada
dispatch (mismo principio de todo el proyecto).
