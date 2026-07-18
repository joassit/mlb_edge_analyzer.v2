# Auditoria de candidatos de historial head-to-head -- resultado real (2026-07-18)

Salida completa de `jsa_historical_historical_candidate_audit.yml` (run
[29625728340](https://github.com/joassit/mlb_edge_analyzer.v2/actions/runs/29625728340)),
corrida sobre las 5 temporadas (2022-2026, 13,101 juegos). Commit base:
`34e49e8` (merge de PR #29).

## Resultado -- linea de investigacion cerrada, NO adoptada

Ninguno de los 4 candidatos (win% head-to-head all-time, ultimos 5
enfrentamientos, diferencia de carreras promedio, ponderado por
recencia) mejora el Brier LOSO de forma estadisticamente significativa
vs. mantener Historical en `advantage=0` (estado real de produccion),
pese a una cobertura excelente (96.1% de los juegos con al menos un
enfrentamiento previo). El unico resultado significativo
(`h2h_win_pct_last_5`, delta_brier_mean=+0.000348, CI +0.000143 a
+0.000562) es un deterioro, no una mejora. Ver `jsa/docs/ROADMAP.md`
("Resultado real de `jsa_historical_historical_candidate_audit.yml` --
linea cerrada, NO adoptada") para la tabla completa y el detalle de la
decision del usuario.

## Archivo

- `historical_candidate_audit_result.json` -- salida completa: auditoria
  descriptiva (cobertura, distribucion de `n_meetings`, distribucion de
  los 4 candidatos) + comparacion LOSO (AUC individual, metricas LOSO si
  se sustituyera, bootstrap CI del delta de Brier vs. Historical=0).

## Alcance exacto del rechazo

Se descartan especificamente estos 4 candidatos -- no el concepto
general de senales de historial. `historical.py` sigue como stub
documentado (`advantage=0` siempre); cualquier propuesta futura debe
partir de una hipotesis distinta (no una variacion parametrica de win%/
run-diff/recencia head-to-head) y pasar de nuevo por validacion LOSO
completa antes de implementarse.
