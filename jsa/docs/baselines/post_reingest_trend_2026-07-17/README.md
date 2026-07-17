# Baseline post-re-ingesta de Trend (2026-07-17)

Resultados reales tras la re-ingesta forzada de las 5 temporadas
(2022-2026) que agrega los 8 campos candidatos de forma reciente para
Trend (schema 3.3 -> 3.4, PR #25/#26). Este baseline **reemplaza** a
`jsa/docs/baselines/pre_trend_2026-07-16/` como referencia para el
desarrollo de Trend -- ver "Incidencia: drift entre el 16 y el 17 de
julio" abajo antes de comparar contra el baseline anterior.

- **Commit SHA de la corrida**: `e63cc7f43ec362f60c0faeddeeb6fa5fdfc9bb95`
  (discriminative-audit run
  [29594444170](https://github.com/joassit/mlb_edge_analyzer.v2/actions/runs/29594444170),
  resolution-audit run
  [29594446086](https://github.com/joassit/mlb_edge_analyzer.v2/actions/runs/29594446086)).
- **Temporadas**: 2022-2026, 13,101 juegos con resultado valido (13,099 en
  el baseline anterior -- +2 juegos de la temporada 2026, en curso).
- **Schema version**: 3.4 (con los 8 campos rolling de Trend ya
  recolectados, pero Trend sigue siendo un stub -- `trend.py` todavia NO
  los usa. `phase1_pillar_stats.trend`/`historical` en el JSON confirman
  `mean=0, std=0, auc=null` en los 13,101 juegos -- cero contribucion,
  igual que antes).
- **Calibracion vigente**: sin cambios respecto al baseline anterior --
  `calibration-evidence_score_raw-v1` sigue siendo la fila activa en
  `calibration_registry` (append-only, no tocada por esta re-ingesta).

## Incidencia: drift entre el 16 y el 17 de julio

Al comparar este baseline contra `pre_trend_2026-07-16/`, las metricas de
los 5 pilares que NO deberian haber cambiado (starter/bullpen/offense/
team_quality/context) mostraron una diferencia real y no trivial pese a
que el codigo de esos pilares es identico:

| Metrica (config de produccion) | 16-jul | 17-jul | Delta |
|---|---|---|---|
| `loso_brier` | 0.245227 | 0.245057 | -0.000169 |
| `loso_ece` | 0.002979 | 0.002026 | -0.000952 |
| `loso_mce` | 0.138187 | 0.053289 | -0.084898 |
| `loso_accuracy` | 0.553783 | 0.553469 | -0.000314 |

Lo mas relevante: temporadas con el **mismo numero exacto de juegos** en
ambas corridas (2023: 2887, 2024: 2838, 2025: 2837) mostraron Brier/
accuracy distintos por temporada -- no es solo un efecto de "hay 2 juegos
mas en 2026".

**Investigacion realizada antes de aceptar este baseline** (sesion
2026-07-17, sin ejecutar cambios de codigo):

1. **Diff de codigo entre el commit del baseline anterior y este**,
   acotado a `point_in_time_provider.py`/`snapshot_reconstruction.py`:
   estrictamente aditivo (los 2 metodos rolling + 8 campos nuevos de
   Trend). Cero lineas tocadas en los metodos preexistentes que alimentan
   starter/bullpen/offense/team_quality/context. **Se descarta un bug de
   codigo como causa.**
2. **Revision de `seed_all()`/registries bajo las 4 ingestas paralelas**
   (2023/2024/2025/2026 corrieron simultaneamente contra el mismo
   `JSA_DATABASE_URL`): `seed_all()` es idempotente por diseno, y durante
   la evaluacion de cada juego los registries solo se LEEN
   (`latest_by_id`), nunca se escriben -- no hay ventana de carrera entre
   los 4 workers. **Se descarta race condition en registries.**
3. **Diff de payloads crudos (juego por juego) NO fue posible**:
   `historical_db.clear_season()` hace un `DELETE` fisico de
   `historical_snapshot` antes de cada re-ingesta con `--force` (necesario
   para que los juegos ya evaluados con la logica vieja se reprocesen con
   los campos nuevos). El baseline del 16-jul solo persistio METRICAS
   AGREGADAS del audit (JSON de resultado), nunca los snapshots crudos por
   juego -- no hay ningun artifact ni backup con los valores punto-en-el-
   tiempo del 16-jul para comparar campo por campo contra los del 17-jul.
4. **Test de determinismo de la API en vivo**: se evaluo pero NO se
   ejecuto -- su resultado (estabilidad de la API en un instante dado)
   no puede confirmar ni descartar si hubo un cambio real entre el 16 y
   el 17 de julio especificamente, solo aportaria evidencia indirecta de
   estabilidad a corto plazo. Se decidio no gastar minutos de Actions en
   una prueba que no cierra la pregunta.

**Conclusion**: con el codigo y las registries descartadas como causa, la
explicacion mas probable es que la MLB Stats API devolvio valores
point-in-time (ERA/OPS/splits de bullpen acumulados via `byDateRange`)
ligeramente distintos entre el 16-jul y el 17-jul para las mismas fechas
de corte de partidos ya jugados -- es decir, el INSUMO externo cambio, no
nuestro calculo. **Esto no es demostrable de forma retrospectiva** porque
los snapshots originales no fueron versionados. Se acepta esta limitacion
metodologica: reingerir contra una fuente externa viva puede producir
variaciones pequenas aun sin cambios de codigo propio.

**Regla para el futuro**: cualquier comparacion historica que requiera
reproducibilidad EXACTA (no solo "misma metrica agregada, orden de
magnitud similar") debe conservar tambien los snapshots crudos o un
artefacto equivalente (ej. dump de `historical_snapshot` completo, no solo
el JSON de resultado del audit) -- las metricas agregadas solas no alcanzan
para diagnosticar retrospectivamente una diferencia si aparece.

## Impacto en las conclusiones ya usadas para decisiones

Ninguna decision tomada hasta ahora se ve afectada:

- El AUC por pilar (invariante de escala, mide orden no magnitud) es
  practicamente identico entre ambas corridas (diferencias en el 4to-5to
  decimal) -- starter 0.5456->0.5457, bullpen 0.5511->0.5519, offense
  0.5465->0.5465, team_quality 0.5324->0.5325, context 0.5004->0.5004.
- Trend/historical siguen 100% inertes en ambas corridas.
- Elo/Pythagorean AUC (calculados 100% offline desde `historical_game`,
  sin depender de la API para estos valores especificos) son practicamente
  identicos: 0.5594->0.5593 (elo), 0.5593->0.5593 (pythagorean).
- La magnitud del drift (Brier ~0.0002, accuracy ~0.03pp a nivel agregado)
  es pequena comparada con cualquier efecto que se este buscando medir en
  la evaluacion de los candidatos de Trend.

## Archivos

- `discriminative_audit_result.json` -- salida completa de
  `jsa_historical_discriminative_audit.yml` (run 29594444170).
- `resolution_audit_result.json` -- salida completa de
  `jsa_historical_resolution_audit.yml` (run 29594446086).

Este es el baseline vigente a partir de aca para evaluar los 4 candidatos
de Trend bajo LOSO (ver ROADMAP.md).
