# Auditoria de candidatos Statcast (H1-H4) -- resultado real (2026-07-18)

Salida completa de `jsa_statcast_candidate_audit.yml` (run
[29664006135](https://github.com/joassit/mlb_edge_analyzer.v2/actions/runs/29664006135)),
corrida sobre las 5 temporadas (2022-2026, 13,101 juegos) tras la
ingesta minima de Statcast (142,515 eventos de bateo reales, ver
`jsa_statcast_minimal_ingest.yml`). Commit base: `7c9e079` (merge de
PR #33).

## Resultado -- linea de investigacion cerrada, NO adoptada

Ninguna de las 4 hipotesis (H1 xwOBA de equipo, H2 xwOBA permitido del
abridor, H3 xwOBA permitido de bullpen, H4 hard-hit rate rolling 7d/14d)
cumple el criterio de exito de 3 condiciones (significancia + tamaño de
efecto minimo `|delta_brier_mean| >= 0.001` + costo justificado, ver
`jsa/docs/statcast_integration_design.md` Seccion 7). H1, H2 y H3
muestran un deterioro ESTADISTICAMENTE SIGNIFICATIVO (los 3 bootstrap CI
quedan enteramente del lado positivo del delta de Brier) -- resultado mas
contundente que Trend/Historical, que solo tuvieron un candidato peor
cada uno. Ver `jsa/docs/ROADMAP.md` para la tabla completa, el detalle
del caveat de cobertura de H2, y la decision del usuario.

## Archivo

- `statcast_candidate_audit_result.json` -- salida completa: comparacion
  LOSO de las 4 hipotesis (AUC individual, cobertura, metricas LOSO si
  se sustituyera, bootstrap CI del delta de Brier vs. el pilar real de
  produccion).

## Alcance exacto del rechazo

Se descartan especificamente estas 4 hipotesis (xwOBA calculado SOLO de
bateos en juego, sin walks/strikeouts) -- no el concepto general de
metricas Statcast ni la arquitectura de ingesta minima
(`historical_statcast_event`, `statcast_ingestion.py`). Una version de
xwOBA que incluya TODOS los resultados de turno al bate resolveria el
problema de cobertura identificado en H2, y es una hipotesis
legitimamente distinta si se retoma esta linea en el futuro.
