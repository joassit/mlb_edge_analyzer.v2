# Auditoria de candidatos Game Flow Engine v1.0 Etapa 1 -- resultado real (2026-07-19)

Salida completa de `jsa_game_flow_candidate_audit.yml` (run
[29669963835](https://github.com/joassit/mlb_edge_analyzer.v2/actions/runs/29669963835)),
corrida sobre las 5 temporadas (2022-2026, 13,101 juegos). A diferencia de
Statcast, no requirio ninguna ingesta previa -- ambas hipotesis se derivan
enteramente de campos ya persistidos en `historical_snapshot`
(`home/away_starter_projected_ip`, `home/away_bullpen_era`). Commit base:
`6d430f9` (merge de PR #34).

## Resultado -- linea de investigacion cerrada, NO adoptada

Ninguna de las 2 hipotesis (GF1 durabilidad del abridor, GF2 dependencia
de bullpen) cumple el criterio de exito de 3 condiciones (significancia
+ tamaño de efecto minimo `|delta_brier_mean| >= 0.001` + costo
justificado, ver `jsa/docs/game_flow_design.md` Seccion 5). Ambas
muestran un deterioro ESTADISTICAMENTE SIGNIFICATIVO (los 2 bootstrap CI
quedan enteramente del lado positivo del delta de Brier) -- mismo patron
que Elo/Pythagorean y Statcast H1-H3. Ver `jsa/docs/ROADMAP.md` para la
tabla completa y la decision del usuario.

## Archivo

- `game_flow_candidate_audit_result.json` -- salida completa: comparacion
  LOSO de las 2 hipotesis (AUC individual, cobertura, metricas LOSO si se
  sustituyera, bootstrap CI del delta de Brier vs. el pilar real de
  produccion).

## Alcance exacto del rechazo

Se descartan especificamente estas 2 transformaciones de `projected_ip`/
`bullpen_era` (Normal con `sigma=1.2` sin calibrar contra IP real por
juego, dependencia lineal de bullpen) -- no el concepto general de
modelar durabilidad/dependencia de bullpen. Un ground truth real de IP
lanzada por juego (via `stats=gameLog`, mucho mas barato que el boxscore
completo) permitiria calibrar `sigma` contra datos reales en vez de un
heuristico, y es una hipotesis legitimamente distinta si se retoma esta
linea en el futuro.
