# Documento de diseño técnico: evaluación de Statcast como fuente de datos nueva

Este documento responde 6 preguntas concretas **antes de escribir código**,
tal como se exigió explícitamente. El objetivo no es decidir si Statcast
se integra -- es dejar planteadas las hipótesis y el protocolo de
validación con el que se van a evaluar, con el mismo estándar de
evidencia (LOSO + bootstrap CI) que ya se aplicó a Trend e Historical
(ver `jsa/docs/ROADMAP.md`, ambas líneas cerradas por falta de evidencia
de mejora). Statcast no entra al proyecto por intuición ni por
justificación teórica sola -- entra solo si supera la misma validación.

Aviso de alcance: partes de este documento (secciones 1 y 3, marcadas
explícitamente) se basan en conocimiento general de dominio sobre
Statcast/Baseball Savant, no en una llamada real verificada desde este
entorno -- este sandbox no tiene salida de red a `baseballsavant.mlb.com`
(mismo bloqueo de proxy ya confirmado contra `statsapi.mlb.com`). Antes
de escribir cualquier código de ingesta real, el primer paso técnico
debe ser un "spike" de verificación (ver Sección 7) ejecutado desde
GitHub Actions (que sí tiene red real), no asumir que los parámetros de
query descritos aquí son exactos.

## 1. Qué métricas de Statcast tienen mayor respaldo para predicción pre-partido

Statcast mide la física del contacto pelota-bate (exit velocity, launch
angle, distancia) y del pitcheo (spin rate, extensión, movimiento), a
diferencia de las métricas de resultado tradicionales (AVG/OPS/ERA) que
ya usa JSA. Las de mayor respaldo en la literatura sabermetría pública
(FanGraphs, Baseball Prospectus, Baseball Savant mismo) para predicción
prospectiva, ordenadas por validación:

1. **xwOBA (expected weighted on-base average)**: la métrica de contacto
   más validada -- combina exit velocity + launch angle (+ sprint speed
   para infield hits) en una sola escala comparable a wOBA. Se
   correlaciona mejor con el talento subyacente que el wOBA real porque
   filtra el componente de suerte de BABIP. Existe tanto para bateadores
   (ofensiva) como "xwOBA permitido" para pitchers.
2. **Barrel rate (Barrel%)**: % de bateos-de-contacto en la zona optima
   de exit velocity + launch angle asociada de forma consistente a
   hits/HR. Estabiliza rapido en muestra chica (util para ventanas
   rolling tipo 7d/14d, el mismo tipo de ventana que ya se probo con
   Trend).
3. **Hard-hit rate (%, exit velocity >=95 mph)**: componente mas simple
   de Barrel%, tambien estabiliza rapido.
4. **Exit velocity promedio**: componente subyacente de los dos
   anteriores, menos informativo aislado.
5. **Chase rate / whiff rate / hard-hit rate permitido** (lado
   pitcheo): equivalentes a xwOBA/Barrel% pero medidos contra el
   pitcher, no el bateador.

Sprint speed y otras metricas de baserunning/defensa tienen respaldo
mucho mas debil para predecir el RESULTADO de un partido especifico
(afectan mas el valor de temporada completa que un juego individual) --
no se consideran candidatos de primera prioridad.

## 2. Qué sustituye información existente vs. qué aporta información nueva

Esto es lo mas importante de aclarar antes de empezar: **Statcast, para
los pilares actuales, es mayormente un REFINAMIENTO, no una dimension
nueva.**

- **xwOBA (bateador) es conceptualmente el mismo dominio que `offense`**
  (OPS relativo a liga) -- la hipotesis no es "esto mide algo que OPS no
  mide", es "esto mide LO MISMO que OPS pero con menos ruido de muestra
  chica, porque filtra suerte de BABIP". Debe evaluarse como
  **alternativa/sustituto** del insumo actual del pilar `offense`, mismo
  patron metodologico que `resolution_audit.py::evaluate_team_quality_
  alternatives()` (Elo/Pythagorean vs. `team_quality`) -- NO como pilar
  nuevo.
- **xwOBA permitido / hard-hit rate permitido (pitcher) es el mismo
  dominio que `starter`/`bullpen`** (ERA con shrinkage) -- misma logica,
  alternativa/sustituto del insumo actual, no dimension nueva.
- **Barrel rate/hard-hit rate a nivel equipo, en ventana rolling (7d/
  14d)** seria conceptualmente muy similar a lo que ya se probo y se
  descarto en Trend (rolling OPS/ERA) -- la diferencia es que la
  METRICA subyacente es distinta (calidad de contacto vs. resultado
  acumulado), no la arquitectura. Vale la pena re-intentar la MISMA
  arquitectura de Trend con un insumo distinto, ya que la regla que se
  documento al cerrar Trend fue "no repetir la misma aproximacion
  esperando otro resultado" -- un insumo de naturaleza fisica distinta
  (contacto, no resultado) SI cumple ese criterio de "hipotesis
  distinta".
- **No hay, dentro de Statcast, una fuente de informacion realmente
  ortogonal a lo que JSA ya mide** (no hay equivalente Statcast de
  clima/contexto, lesiones, o historial head-to-head) -- Statcast vive
  enteramente en el dominio de "calidad de bateo/pitcheo", que ya esta
  cubierto conceptualmente por 3 de los 4 pilares imprescindibles
  (starter/bullpen/offense). La pregunta de investigacion correcta es
  "¿la version Statcast de esta señal predice mejor que la version
  actual?", no "¿esto agrega una dimension nueva?".

## 3. Disponibilidad histórica, cobertura y estabilidad de la fuente

*(Seccion basada en conocimiento general de dominio -- ver aviso de
alcance arriba, verificar con un spike antes de construir.)*

- **Cobertura historica**: Statcast esta disponible para las 30 franquicias
  desde la temporada 2015 (instrumentacion completa en todos los
  estadios). Las 5 temporadas ya ingeridas por JSA (2022-2026) estan
  comodamente dentro de esa ventana -- no hay gap de cobertura como
  hubo que considerar para otras fuentes mas nuevas.
- **Acceso**: a diferencia de `stats.mlb.com` (la MLB Stats API que ya
  usa `point_in_time_provider.py`, documentada y estable), las metricas
  Statcast agregadas NO tienen un endpoint oficial documentado
  equivalente -- el acceso publico estandar es via Baseball Savant
  (`baseballsavant.mlb.com`), cuyo export CSV de busqueda es el mismo
  que usan librerias de terceros como `pybaseball` (no es una API
  oficial versionada, es la misma consulta que usa el frontend del
  sitio). **Esto es un riesgo real de estabilidad** (sin SLA, sin
  garantia de que los parametros de query no cambien) que debe pesar en
  la decision de costo/beneficio.
- **Point-in-time**: el export de Baseball Savant soporta filtros de
  fecha (`game_date_gt`/`game_date_lt` o equivalente) que en principio
  permitirian el mismo patron `byDateRange` ya usado en todo el proyecto
  -- pero los nombres exactos de parametros y la forma de la respuesta
  deben confirmarse con una llamada real, no asumirse de memoria.

## 4. Costo de ingesta, almacenamiento y mantenimiento

- **Ingesta**: mismo patron de costo que ya se vio con Trend (8 campos
  nuevos rolling agregaron llamadas HTTP por juego) -- cada campo
  Statcast nuevo (por equipo, por ventana) es una llamada adicional por
  juego. Con la experiencia real de las re-ingestas de Trend (2-5 horas
  por temporada), agregar Statcast probablemente extienda ese tiempo de
  forma proporcional al numero de metricas evaluadas -- razon de mas
  para acotar la Fase 0 de validacion a 1-2 metricas maximo antes de
  pensar en una re-ingesta completa de las 5 temporadas.
- **Almacenamiento**: aditivo a `GameSnapshot`, mismo patron de
  migracion de schema ya usado (3.3->3.4 para Trend; esto seria una
  migracion nueva, ej. 3.4->3.5), footprint marginal pequeño por campo.
- **Mantenimiento**: mayor que la MLB Stats API oficial, precisamente
  por no ser un endpoint documentado/versionado -- requiere el mismo
  patron defensivo ya establecido en todo el proveedor (`None` si la
  llamada falla, nunca lanzar excepcion que aborte la ingesta de un
  juego individual), y probablemente un caché mas agresivo dado el
  riesgo de rate-limiting no documentado.

## 5. Riesgos de leakage temporal

- **Mismo principio ya establecido en todo el proyecto**: cualquier
  metrica Statcast usada para un juego en fecha `D` debe calcularse
  SOLO con eventos de fecha `< D` (nunca `stats=season` ni su
  equivalente Statcast -- siempre un filtro de fecha estricto anterior
  al corte, mismo patron que `_end_date()` en `point_in_time_provider.py`).
- **Riesgo especifico de roster**: una metrica de equipo (ej. xwOBA de
  equipo en los ultimos 7 dias) debe calcularse solo con los jugadores
  que efectivamente jugaron para ese equipo en esas fechas -- un trade
  o call-up posterior no debe contaminar la ventana retroactivamente
  (mismo principio ya aplicado al roster-as-of-date del proyecto
  hermano y a `team_ops_rolling_as_of()`).
- **Riesgo especifico de Statcast**: la definicion tecnica de "Barrel"
  (combinacion exacta de exit velocity + launch angle que califica) ha
  tenido ajustes menores por parte de MLB en el pasado -- si la fuente
  de datos aplica retroactivamente una definicion actualizada de forma
  inconsistente entre temporadas, eso podria producir un drift similar
  al que ya se investigo y documento para la re-ingesta de Trend
  (ver ROADMAP.md, "Re-ingesta real de las 5 temporadas... + drift").
  Debe verificarse que la fuente use una definicion consistente en toda
  la ventana 2022-2026 antes de confiar en comparaciones entre
  temporadas.

## 6. Hipótesis concretas a validar (mismo protocolo LOSO + bootstrap CI)

A diferencia de Trend/Historical (que competian contra un pilar en 0,
la barra mas baja posible), los candidatos Statcast compiten contra
pilares YA clasificados como **imprescindibles** en el diagnostico del
techo del modelo -- la barra es mas alta. Hipotesis propuestas, en orden
de prioridad:

1. **H1 -- xwOBA de equipo (temporada, con shrinkage equivalente a
   `offense_factor`) como sustituto del insumo de `offense`**: ¿el
   `offense_factor(xwOBA, liga_xwOBA)` predice mejor en LOSO que
   `offense_factor(OPS, liga_OPS)`? Mismo patron que la Fase 8 de
   sensibilidad de discretizacion en `resolution_audit.py`, pero
   cambiando el INSUMO, no la representacion discreta.
2. **H2 -- xwOBA permitido / hard-hit rate permitido de abridor como
   sustituto del insumo de `starter`**: analogo a H1, mismo patron que
   `shrunk_era()` pero sobre una metrica Statcast en vez de ERA.
3. **H3 -- xwOBA/hard-hit rate permitido de bullpen agregado como
   sustituto del insumo de `bullpen`**: analogo a H2 para el pilar
   `bullpen`.
4. **H4 -- rolling Barrel%/hard-hit rate de equipo (7d/14d) como
   candidato de Trend**, re-intentando la arquitectura ya construida en
   `trend_candidate_audit.py` pero con un insumo de naturaleza distinta
   (contacto, no resultado) -- cumple la regla de "hipotesis distinta"
   documentada al cerrar la linea anterior de Trend.

Cada hipotesis se evalua exactamente igual que Trend/Historical: LOSO
completo sobre las 5 temporadas, bootstrap CI de 500 resamples del delta
de Brier contra el pilar ACTUAL (no contra 0). El criterio de adopcion
completo (significancia + tamaño de efecto + costo) se detalla en la
Seccion 7 -- significancia estadistica sola NO alcanza. Se prioriza
H1-H3 sobre H4 porque compiten contra un insumo ya validado (mayor rigor
de la prueba) y porque, si NINGUNA de H1-H3 supera a su version actual,
eso seria evidencia adicional de que el techo de informacion no esta en
la precision de la metrica de bateo/pitcheo sino en otro lado -- un
resultado diagnostico valioso en si mismo, igual que lo fue el cierre de
Trend/Historical.

## 7. Criterios de éxito y fracaso (cuándo aceptar, cuándo descartar)

Definidos ANTES de correr el protocolo, para no ajustar el criterio
despues de ver el resultado (la misma disciplina que ya goberno Trend/
Historical). Una hipotesis (H1-H4) se **acepta** solo si cumple las
TRES condiciones a la vez -- ninguna es suficiente por si sola:

1. **Significancia estadistica real**: `delta_brier_mean < 0` (mejora,
   no empeoramiento) Y bootstrap CI de 500 resamples enteramente del
   lado de la mejora (`significant=True`, mismo criterio ya usado en
   Trend/Historical/ablacion) -- sobre LOSO completo de las 5
   temporadas, nunca sobre una sola temporada ni sobre metricas
   in-sample.
2. **Tamaño de efecto minimo, no solo significancia**: una mejora
   estadisticamente significativa pero de magnitud despreciable NO
   justifica adoptar el candidato. Umbral propuesto: `|delta_brier_mean|
   >= 0.001` (el mismo orden de magnitud que separo "imprescindible" de
   "neutro" en la ablacion de la Seccion de diagnostico del ROADMAP,
   donde los pilares imprescindibles mostraron deltas de 0.0007-0.0014)
   -- una mejora de 0.00005, aunque el CI no cruce cero, se considera
   **irrelevante en la practica** y no se adopta. Este umbral es una
   propuesta inicial, ajustable con el usuario antes de correr el
   protocolo, pero debe fijarse ANTES de ver los resultados reales.
   Ademas del delta de Brier, se reporta el delta de ECE y de Log-loss
   como contexto (una mejora de Brier que empeora la calibracion ECE de
   forma notoria es una señal de alerta, no un exito limpio).
3. **El costo operativo debe justificarse**: dado el riesgo de
   estabilidad de la fuente (Seccion 3: sin API oficial documentada) y
   el costo de ingesta/mantenimiento (Seccion 4), una mejora que SI
   cumple 1 y 2 pero es marginal (cerca del umbral minimo) debe
   sopesarse explicitamente contra ese costo antes de integrarse a
   produccion -- no es una aprobacion automatica solo por pasar el
   filtro estadistico. Esta evaluacion de costo/beneficio se hace con el
   usuario, no de forma automatica por el protocolo.

**Criterio de fracaso/cierre de linea** (mismo que ya se aplico a Trend/
Historical): si NINGUNA de H1-H4 cumple las 3 condiciones, la linea de
Statcast se cierra documentando el resultado real (igual que las
secciones de cierre de Trend/Historical en el ROADMAP) -- no se declara
Statcast "descartado en general" (ver Seccion 2: la conclusion se limita
a las metricas y ventanas concretas evaluadas), pero tampoco se sigue
intentando variaciones parametricas de las mismas 4 hipotesis esperando
un resultado distinto sin una razon nueva.

## Próximo paso (no iniciar sin confirmación)

Antes de escribir cualquier código de ingesta:

1. Spike de verificación (GitHub Actions, no este sandbox) contra
   Baseball Savant: confirmar forma real de la respuesta, parámetros de
   fecha exactos, y estabilidad de al menos 1 metrica (xwOBA de equipo)
   para un rango de fechas conocido.
2. Solo si el spike confirma viabilidad, construir la infraestructura
   de ingesta (nuevo `StatcastProvider`, aditivo a `point_in_time_
   provider.py` o modulo separado, TBD segun lo que muestre el spike).
3. Recolectar candidatos SIN re-ingerir las 5 temporadas completas
   todavia -- mismo criterio de cautela que se aplico a Trend (PR
   separado para recolectar candidatos, confirmar con el usuario antes
   de disparar una re-ingesta real de horas).
4. Evaluar H1-H4 con LOSO + bootstrap CI antes de tocar `offense.py`/
   `starter.py`/`bullpen.py`/`trend.py`.
