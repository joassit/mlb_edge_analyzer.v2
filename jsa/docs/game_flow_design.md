# Game Flow Engine v1.0 -- Etapa 1 (diseño técnico)

## 1. Contexto

Tras la auditoría del Game Flow actual (2026-07-19, ver conversación /
ROADMAP.md), se confirmó que JSA no modela el desarrollo temporal de un
partido -- evalúa un `GameSnapshot` estático pre-partido. El usuario
propuso un Game Flow Engine completo (Starter Projection, Bullpen
Projection, Closer Projection, dominancia por fases del juego, pesos
dinámicos, Win State Projection). Verificado contra el código real, la
mayoría de esas variables no tienen dato hoy (matchup vs. lineup, pitch
count real, días de descanso, forma reciente -- ya evaluada y rechazada
como Trend --, xFIP/WHIP/K%/BB% del cerrador).

Alcance acordado para esta Etapa 1: **únicamente** lo construible con
datos ya persistidos en `historical_snapshot`, sin ninguna ingesta nueva,
como un módulo de generación de candidatos (mismo rol que
`trend_candidate_audit.py`/`historical_candidate_audit.py`/
`statcast_candidate_audit.py`/`resolution_audit.py`), nunca wireado
directamente a `engine/pillars/` ni a `BASE_PILLAR_WEIGHTS` hasta
demostrar mejora real.

## 2. Hipótesis evaluadas

### GF1 -- `gf1_starter_durability`

Sustituye el insumo de `starter` (hoy: ERA con shrinkage,
`starter.py::evaluate()`) por un diff de "probabilidad de completar >=6
entradas" (quality start), derivado de `home/away_starter_projected_ip`
-- el mismo proxy (IP por salida de temporada) que ya usa
`context_detector.py` para `long_outing`/`short_outing_bullpen_game`.

`Prob(IP >= 6) = 1 - Φ((6 - projected_ip) / sigma)`, con
`sigma=1.2` (heurístico de partida, ver limitación abajo).

Prueba si la **durabilidad esperada** (cuánto dura, en promedio, el
abridor) predice el resultado de forma distinta a su **calidad de
pitcheo** (ERA) -- dos conceptos relacionados pero no idénticos: un
abridor puede tener ERA bajo en salidas cortas, o ERA mediocre en salidas
largas.

### GF2 -- `gf2_bullpen_dependency`

Sustituye el insumo de `bullpen` (hoy: ERA con shrinkage + penalización
de closer, `bullpen.py::evaluate()`) por ese mismo diff, escalado por
cuánto se espera que dependa el partido del bullpen:

```
expected_bullpen_ip(equipo) = max(1.0, 9 - projected_ip(su abridor))
dependency_factor = promedio(expected_bullpen_ip home, away) / 9
gf2 = era_diff_con_shrinkage_y_closer * dependency_factor
```

Prueba la hipótesis: la ventaja de bullpen importa más en partidos donde
ambos equipos van a apoyarse más en él (salidas cortas esperadas de
ambos abridores), y menos cuando ambos abridores proyectan salidas
profundas.

## 3. Limitación honesta: sin ground truth de IP real por juego

`historical_game` (la única tabla de resultados reales) solo persiste
`home_score`/`away_score`/`winner` -- **no existe en ningún lado del
proyecto un registro de cuántas entradas lanzó efectivamente el abridor
en un juego histórico específico**. Esto significa que `sigma=1.2` en
GF1 (la dispersión asumida alrededor de `projected_ip`) es un heurístico
sin calibrar contra resultados reales de IP por juego -- mismo criterio
de honestidad que `SHRINKAGE_K_IP=60`/`OFFENSE_FACTOR_EXPONENT=1.8` en
`config.py` ("puntos de partida de literatura, no calibrados todavía").

Consecuencia metodológica: GF1/GF2 **no se validan calibrando su propia
probabilidad interna** (no se puede responder "¿cuándo decimos 70%,
acierta 70% de las veces?" sin ese dato) -- se validan exactamente igual
que Elo/Pythagorean/Statcast: sustituyendo el diff en el pilar objetivo y
midiendo si el Evidence Score resultante predice mejor `home_win` via
LOSO + bootstrap CI. Es una pregunta distinta pero igual de válida bajo
el protocolo de este proyecto: "¿esta representación de la información ya
existente mejora la predicción del resultado?", no "¿esta probabilidad
está bien calibrada?".

Obtener el ground truth real (IP efectivamente lanzada por el abridor en
cada juego) sería posible vía `people/{id}/stats?stats=gameLog&group=
pitching&season=X` (game log por pitcher, mismo patrón de llamada que
`get_pitcher_era_ip()`) -- mucho más barato que el boxscore/linescore
completo de equipo que requeriría hits/HR/LOB/entradas extra. Esto queda
como una posible Etapa 1b, **no autorizada todavía**, si se decide validar
la calibración interna del modelo de durabilidad en el futuro.

## 4. Deliberadamente fuera de esta Etapa 1

**Closer Rating** (separar el ERA del cerrador del resto del bullpen):
requiere un campo NUEVO en `historical_snapshot`.
`point_in_time_provider.py::bullpen_era_as_of()` ya calcula el ERA
individual de cada relevista para identificar al cerrador (necesita esa
cifra para comparar saves), pero lo descarta después de agregarlo al
ERA de bullpen conjunto -- nunca lo persiste por separado. Igual que
cualquier campo nuevo anterior (Trend, fielding%), agregarlo exige su
propia re-ingesta de las 5 temporadas (horas de GitHub Actions) y su
propia autorización explícita antes de correrla.

**Dominancia por fases (Early/Middle/Late Game), Win State Projection,
pesos dinámicos, First 5 Innings**: requieren boxscore/linescore real
(hits, HR, LOB, carreras por entrada) -- ninguno ingerido en este
proyecto. Quedan para una Fase 2/3 posterior, condicionadas a una nueva
ingesta explícitamente autorizada (mismo patrón que el spike de
factibilidad + ingesta mínima que se siguió para Statcast).

## 5. Criterio de éxito y fracaso

Mismo criterio de 3 condiciones que Statcast
(`statcast_integration_design.md` Sección 7), aplicado a GF1/GF2:

1. `delta_brier_mean < 0` (mejora) Y `significant=True` (CI de bootstrap
   enteramente del lado de la mejora).
2. `|delta_brier_mean| >= 0.001` (tamaño de efecto mínimo).
3. El costo operativo de mantener la integración es explícitamente
   justificado por el usuario -- nunca automático.

Solo si las 3 se cumplen a la vez se abre una propuesta separada para
integrar GF1/GF2 a `starter.py`/`bullpen.py` -- este módulo, igual que
`resolution_audit.py`/`statcast_candidate_audit.py`, nunca escribe a
`engine/pillars/` ni a `BASE_PILLAR_WEIGHTS` por sí mismo.

## 6. Implementación

- `jsa/historical/game_flow_candidate_audit.py` -- cómputo de diffs +
  comparación LOSO (mismo patrón que `statcast_candidate_audit.py`).
- CLI: `python -m jsa.historical.cli game-flow-candidate-audit --db ...
  --season ...`
- `.github/workflows/jsa_game_flow_candidate_audit.yml` --
  `workflow_dispatch`, solo lectura, sin ingesta nueva (a diferencia de
  Statcast, no requiere ningún workflow de ingesta previo -- los datos ya
  están en `historical_snapshot` desde la ingesta original).
- Tests: `jsa/tests/test_game_flow_candidate_audit.py` (8 tests: formas,
  monotonicidad de las funciones auxiliares, sanity checks anti-fuga
  --coinflip puro y recuperación de señal inyectada--, y punta a punta).
