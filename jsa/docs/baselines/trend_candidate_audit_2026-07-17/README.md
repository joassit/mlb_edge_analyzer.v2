# Auditoria de candidatos de Trend -- resultado real (2026-07-17)

Salida completa de `jsa_historical_trend_candidate_audit.yml` (run
[29621086180](https://github.com/joassit/mlb_edge_analyzer.v2/actions/runs/29621086180)),
corrida sobre las 5 temporadas (2022-2026, 13,101 juegos) tras fijar el
baseline de `post_reingest_trend_2026-07-17/`. Commit base: `9bc374d`
(merge de PR #27).

## Resultado -- linea de investigacion cerrada, NO adoptada

Ninguno de los 4 candidatos (OPS/ERA rolling de equipo, 7d/14d) mejora el
Brier LOSO de forma estadisticamente significativa vs. mantener Trend en
`advantage=0` (estado real de produccion). El unico resultado
significativo (`era_rolling_14d`, delta_brier_mean=+0.000340, CI
+0.000142 a +0.000542) es un deterioro, no una mejora. Ver
`jsa/docs/ROADMAP.md` ("Resultado real de
`jsa_historical_trend_candidate_audit.yml` -- linea cerrada, NO
adoptada") para la tabla completa y el detalle de la decision del
usuario.

## Archivo

- `trend_candidate_audit_result.json` -- salida completa: auditoria
  descriptiva (cobertura/distribucion/correlacion cruzada de los 8
  campos rolling) + comparacion LOSO de los 4 candidatos (AUC individual,
  metricas LOSO si se sustituyera, bootstrap CI del delta de Brier vs.
  Trend=0).

## Por que se conserva esto

`trend.py` sigue como stub documentado (`advantage=0` siempre) -- esta
decision no es una limitacion pendiente, es la conclusion correcta de un
experimento que se corrio con evidencia real. Este archivo existe para
que, si en el futuro alguien propone re-evaluar rolling OPS/ERA de
equipo a 7d/14d "por si acaso", la respuesta ya este documentada con
numeros reales en vez de tener que volver a correr el audit.
