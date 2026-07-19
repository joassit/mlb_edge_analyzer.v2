# Evaluación de la propuesta "Evolución del Modelo MLB Edge Analyzer" contra evidencia ya generada (2026-07-19)

## 1. Por qué este documento existe

El usuario entregó una propuesta extensa (10 secciones: fortaleza de
equipo, matchup de pitcheo, dominancia por entradas, gestión dinámica de
pitcheo, Game Flow Engine, especialización por mercado, proceso de
validación) para crear como nueva rama del proyecto. Antes de escribir
código de producción se verificó, sección por sección, si alguna parte ya
tiene evidencia real generada en este mismo repositorio -- porque el
**Principio rector #2 de la propia propuesta** dice explícitamente:
"Incorporar nuevas variables únicamente si demuestran mejorar el
rendimiento mediante backtesting", y el Principio #3: "Evitar variables
redundantes o con bajo poder predictivo".

Resultado de esa verificación: **la mayoría de las Secciones 3, 5, 6 y 7
de la propuesta ya se probaron, hoy mismo (2026-07-19), bajo el protocolo
de validación más riguroso que exige la Sección 9 de la propuesta
(LOSO + bootstrap CI + tamaño de efecto mínimo) -- y se rechazaron por
evidencia real, no por falta de intento.** Ver `jsa/docs/ROADMAP.md`,
secciones "`team_quality`: Elo y Pythagorean..." y "Game Flow Engine
v1.0..." (ambas 2026-07-19), y las tres líneas anteriores (Trend,
Historical, Statcast H1-H4) que comparten el mismo patrón subyacente.

Escribir esas partes otra vez en el proyecto legado (`model/`, sin la
infraestructura de validación de `jsa/`) violaría el propio Principio #2
de la propuesta: se estaría agregando código de producción sin evidencia,
sabiendo que la evidencia ya existe y es negativa.

## 2. Cruce sección por sección

| Sección de la propuesta | Contenido | Estado real en este repo | Evidencia |
|---|---|---|---|
| 3.1 Rating ELO dinámico | ELO como sustituto de fortaleza general | **CERRADO -- rechazado** | `resolution_audit.py::compute_elo_and_pythagorean()`, AUC 0.559, ΔBrier +0.000460 (peor, no significativo por tamaño de efecto) |
| 3.2 Producción ofensiva (carreras, HR, hits, eficiencia carreras/hits) | Pythagorean Expectation es la formalización estándar de "carreras generadas/permitidas" | **CERRADO -- rechazado** | Mismo audit, Pythagorean AUC 0.559, ΔBrier +0.000479 (peor) |
| 3.4 Posición en tabla, récord W-L | El propio documento ya las marca "requieren validación" | **CERRADO -- rechazado** | ELO es matemáticamente equivalente a W-L acumulado; mismo resultado que 3.1 |
| 4.1 Pitcher vs Pitcher (xERA, FIP, xFIP, SIERA, Hard Hit%, Barrel%) | Métricas Statcast/expected stats | **CERRADO -- rechazado (proxy más cercano probado)** | `statcast_candidate_audit.py`, H2 (xwOBA permitido por abridor), ΔBrier +0.001233, peor y significativo |
| 4.2 Pitcher vs Equipo (wRC+ del lineup, Chase Rate, xwOBA) | Matchup pitcher-lineup | **ABIERTO -- no probado, bloqueado por datos** | xwOBA de equipo (H1) sí se probó y fue peor; Chase Rate específicamente **nunca se implementó** (solo mencionado como candidato de prioridad 5 en `statcast_integration_design.md` Sección 1) -- requiere ingesta nueva de datos de pitch-level zona/swing, no solo bateo-en-juego |
| 4.4 Rating del Bullpen | Ya existe como pilar `bullpen` con shrinkage + penalización de closer | **YA IMPLEMENTADO** (no es una variable nueva) | `jsa/engine/pillars/bullpen.py` |
| 4.5 Rating del Cerrador (separado del bullpen) | Closer Rating aislado | **ABIERTO -- explícitamente bloqueado, no autorizado** | `game_flow_design.md` Sección 4: requiere campo nuevo en `historical_snapshot` + re-ingesta de 5 temporadas |
| 4.6 Cuatro calificaciones independientes del abridor (calidad/forma reciente/matchup/durabilidad) | Descompone `starter` | **3 de 4 sub-componentes CERRADOS**: forma reciente = Trend (cerrado), durabilidad = GF1 (cerrado, ΔBrier +0.000911 peor). Matchup específico = mismo hueco que 4.2 | Ver filas de Trend y Game Flow |
| 5. Inning Dominance Rating (IDR), Early/Middle/Late Game | Dominancia por fases del juego | **ABIERTO -- explícitamente bloqueado, no autorizado** | `game_flow_design.md` Sección 4: requiere boxscore/linescore real (hits, HR, LOB, carreras por entrada), ningún campo de estos está ingerido hoy |
| 6.1-6.4 Gestión dinámica de pitcheo (SWR, peso dinámico bullpen/closer por innings esperados) | Exactamente GF2 (`gf2_bullpen_dependency`: escala la ventaja de bullpen por `expected_bullpen_ip`) | **CERRADO -- rechazado** | `game_flow_candidate_audit.py`, GF2 AUC 0.561, ΔBrier +0.000391 (peor, significativo) |
| 7. Game Flow Engine (reconstrucción del flujo esperado) | Concepto general | **CERRADO en su Etapa 1 concreta (GF1+GF2)** -- la infraestructura (`game_flow_candidate_audit.py`) queda disponible para hipótesis futuras con datos nuevos (IP real vía `gameLog`, boxscore) | `ROADMAP.md`, "Resultado real de `jsa_game_flow_candidate_audit.yml`" |
| 8.1 Modelo First 5 Innings | Especialización por mercado, mínima influencia del bullpen | **ABIERTO -- no modelado en ningún proyecto de este repo.** JSA predice solo resultado de 9 entradas (`home_win`); el proyecto legado tiene picks de F5 en `main.py`/`model/picks.py` pero sin un modelo propio (usa el mismo modelo de 9 entradas con un comentario explícito de que "F5 suele ser más corta", `model/runs_projection.py:55`) | Verificado por búsqueda directa en el código, sin hallazgos de un modelo F5 dedicado |
| 8.2 Modelo Moneyline | Ya es el modelo actual de JSA (7 pilares) | **YA IMPLEMENTADO** (no es una variable nueva) | -- |
| 8.3 Modelo Totales | Especialización por mercado de totales | **ABIERTO -- no modelado**, mismo hueco que 8.1 (requiere ground truth de carreras totales, que sí existe vía `home_score`/`away_score`, a diferencia de F5) | -- |
| 9. Proceso de validación (ROC-AUC, Brier, SHAP, backtesting) | Protocolo de aceptación de variables nuevas | **YA IMPLEMENTADO, y es exactamente el protocolo que ya se aplicó para cerrar las 5 líneas de arriba** | `jsa/historical/discriminative_audit.py`, `resolution_audit.py`, `statcast_candidate_audit.py`, `game_flow_candidate_audit.py` |

## 3. Lo que queda genuinamente abierto

Descontando lo ya cerrado y lo ya implementado, quedan tres líneas reales:

1. **Matchup Pitcher vs Lineup específico** (Sección 4.2-4.3): wRC+ o
   xwOBA del lineup contra la mano del abridor (LHP/RHP), Chase Rate. No
   es una repetición de Statcast H1 (que usó xwOBA de equipo sin separar
   por mano) -- es una hipótesis genuinamente distinta. Bloqueada porque
   requiere splits vs. mano por juego, no solo temporada acumulada, y
   Chase Rate requiere datos pitch-level (zona/swing) que
   `historical_statcast_event` no ingiere hoy (solo guarda
   `launch_speed` + `estimated_woba_using_speedangle` por evento de
   bateo).
2. **Especialización por mercado First 5 / Totales** (Sección 8.1, 8.3):
   no existe en ningún proyecto de este repo. Totales es factible sin
   ingesta nueva (el ground truth -- carreras totales del juego -- ya
   existe); First 5 está bloqueado por la misma razón que Inning
   Dominance (Sección 5): no hay linescore por entrada ingerido, así que
   no existe forma de saber punto-en-el-tiempo si un equipo "ganó" sus
   primeras 5 entradas en un juego histórico.
3. **Inning Dominance / Closer Rating** (Secciones 4.5, 5): ambas ya
   señaladas como bloqueadas y no autorizadas en `game_flow_design.md`
   antes de que llegara esta propuesta -- siguen en el mismo estado.

## 4. Patrón a seguir si se autoriza avanzar

Este repo ya tiene un patrón repetido 5 veces (Trend, Historical,
Statcast, Elo/Pythagorean, Game Flow) para evaluar una variable nueva sin
arriesgar producción:

1. Spike de factibilidad (¿la fuente de datos responde, cubre el
   histórico, tiene point-in-time safety?) -- ver
   `statcast_integration_design.md` Sección 7 como plantilla.
2. Ingesta mínima aislada en una tabla nueva, nunca mezclada con
   `historical_snapshot` de producción.
3. `*_candidate_audit.py`: cómputo point-in-time-safe + comparación LOSO
   + bootstrap CI de 500 resamples contra el pilar real de producción.
4. Criterio formal de 3 condiciones (`statcast_integration_design.md`
   Sección 7): `delta_brier_mean < 0` Y `significant=True` Y
   `|delta_brier_mean| >= 0.001`.
5. Solo si las 3 se cumplen a la vez, una propuesta separada para
   wirear a `engine/pillars/` -- nunca automático.

Cada paso (spike -> ingesta -> ejecutar el audit) requiere autorización
explícita del usuario antes de correr, exactamente igual que las 5 líneas
anteriores -- ninguna se disparó sin esa confirmación.

## 5. Recomendación de siguiente paso

De las 3 líneas abiertas (Sección 3 arriba), **Totales** es la de menor
costo: no requiere ninguna ingesta nueva (el resultado ya existe como
`home_score + away_score`), y JSA hoy no tiene ningún candidato de pilar
orientado a ese mercado específico -- sería la primera especialización de
mercado real del proyecto. Matchup-vs-mano y First 5/Closer Rating/Inning
Dominance requieren ingesta nueva no autorizada todavía.

Este documento no ejecuta ningún spike ni ninguna ingesta -- deja
planteado el mapa completo para que el usuario decida cuál de las 3
líneas abiertas autorizar primero, con el mismo nivel de evidencia que ya
exige el resto del proyecto.
