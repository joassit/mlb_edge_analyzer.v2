# Auditoria de candidatos Game Flow Engine v1.0 Etapa 1 (GF1/GF2) -- resultado real (2026-07-19)

Salida completa de `jsa-game-flow-candidate-audit` (run
[29669963835](https://github.com/joassit/mlb_edge_analyzer.v2/actions/runs/29669963835)),
corrida sobre las 5 temporadas (2022-2026, 13,101 juegos) contra los
datos ya persistidos en `historical_snapshot` -- sin ninguna ingesta
nueva. Commit base: `6d430f9` (merge de PR #34, mismo commit en el que
se construyo el modulo). Ver `jsa/docs/game_flow_design.md` para el
diseno tecnico y `jsa/docs/ROADMAP.md` para el detalle completo.

## Resultado -- ninguna hipotesis cumple el criterio de 3 condiciones

Criterio pre-acordado (`game_flow_design.md` Seccion 5, mismo patron que
Statcast/Trend/Historical/Elo-Pythagorean): `delta_brier_mean < 0` Y
`significant=True` Y `|delta_brier_mean| >= 0.001`, las 3 a la vez.

| Hipotesis | Pilar objetivo | AUC individual | Cobertura | Δ Brier vs. actual | Significativo | \|Δ\| >= 0.001 | Cumple los 3 criterios |
|---|---|---|---|---|---|---|---|
| `gf1_starter_durability` | starter | 0.522 | 83.5% | **+0.000911** | Si (CI: [0.000397, 0.001443]) | No (queda debajo) | **No** |
| `gf2_bullpen_dependency` | bullpen | 0.561 | 86.1% | **+0.000391** | Si (CI: [0.0000645, 0.000765]) | No | **No** |

Ambas hipotesis fallan en 2 de las 3 condiciones a la vez: el delta de
Brier es **positivo** (serian PEORES que el insumo actual de
`starter`/`bullpen`, no mejores) Y estadisticamente significativo (el CI
de bootstrap de ambas queda enteramente del lado positivo) -- mismo
patron de "doble falla" que Elo/Pythagorean para `team_quality`, no solo
"sin evidencia de mejora". El tamaño de efecto de GF1 (~0.00091) queda
cerca pero por debajo del minimo de `0.001` exigido; el de GF2 (~0.00039)
queda claramente por debajo.

**Nota sobre GF2**: a pesar de tener el AUC individual mas alto de las
dos hipotesis (0.561, superior incluso al AUC actual reportado de
`bullpen`, 0.552, en el diagnostico del techo del modelo), sustituir el
insumo completo de `bullpen` por el diff escalado por dependencia empeora
el Evidence Score combinado de forma significativa -- mismo patron ya
observado con Elo/Pythagorean en `team_quality`: un AUC individual mas
alto en la variable candidata no garantiza una mejora al sustituir el
insumo del pilar completo.

## Archivo

- `game_flow_candidate_audit_result.json` -- salida completa: AUC/KS/
  correlacion/MI individual, metricas LOSO si se sustituyera cada
  hipotesis (agregado y por temporada), metricas LOSO reales del pilar
  actual por temporada, y bootstrap CI del delta de Brier vs. produccion.

## Alcance exacto del rechazo

Se descartan especificamente estas 2 hipotesis (GF1: sustituir `starter`
por un diff de probabilidad de completar >=6 entradas derivado de
`projected_ip`, sigma=1.2 sin calibrar; GF2: sustituir `bullpen` por el
mismo diff escalado por dependencia esperada de bullpen) -- no el
concepto general de modelar el flujo del partido. La limitacion honesta
documentada en el diseno (sin ground truth de IP real por juego, `sigma`
heuristico sin calibrar) sigue siendo la sospecha mas probable de por que
GF1 en particular no mejora: la Etapa 1b (obtener IP real via
`stats=gameLog` por pitcher, no autorizada todavia) permitiria calibrar
`sigma` contra datos reales en vez de un heuristico, y es una hipotesis
legitimamente distinta si se retoma esta linea. Etapas posteriores
(Closer Rating independiente, dominancia por fases, Win State Projection,
pesos dinamicos) siguen bloqueadas por falta de boxscore/linescore
ingerido, sin relacion con este resultado.
