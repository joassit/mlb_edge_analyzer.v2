# MLB Edge Analyzer

Sistema propio para comparar la probabilidad de tu modelo contra el mercado
de apuestas de MLB. Construido por etapas, empezando 100% gratis.

> **Proyecto hermano:** [`jsa/`](jsa/README.md) es una reconstruccion
> completa de este pipeline sobre una arquitectura de investigacion
> reproducible y auditable (JSA v3.0 — Manifest firmado, Confidence Gate,
> Registries de extensibilidad), aplicando las lecciones operativas reales
> de este proyecto (ver `jsa/README.md` y `jsa/docs/ROADMAP.md`). Vive en
> este mismo repo, corre en su propio workflow de GitHub Actions
> (`.github/workflows/jsa_daily_pipeline.yml`) y no comparte codigo ni base
> de datos con lo de abajo.

## Etapa 1 (esta versión) — 100% gratuita

- ✅ Obtener partidos del día (MLB Stats API, oficial y gratuita)
- ✅ Obtener pitchers probables/confirmados
- ✅ Obtener ERA de pitchers y OPS de equipos (MLB Stats API)
- ✅ Calcular probabilidad del modelo (heurístico + Skellam), no placeholders
- ✅ Cuotas en vivo (The Odds API) con fallback a `MARKET_ODDS` manual, con
      caché + presupuesto mensual para no agotar el free tier
- ✅ Edge, EV y consenso sin vig (no-vig) contra el mercado
- ✅ Favorito del mercado explícito (equipo + probabilidad) en consola,
      CSV y dashboard, con edge del modelo específicamente contra ese favorito
- ✅ Candidatos a revisión marcados automáticamente (edge por encima de un
      umbral + los dos modelos de acuerdo en el favorito) — nunca una apuesta
      automática, solo una preselección
- ✅ Picks recomendados por partido en Moneyline/Run Line/Totales (0 a 3,
      uno por mercado), con picks forzados marcados y separados en las
      métricas cuando ningún mercado tiene edge real
- ✅ Guardar histórico en base de datos (SQLite por defecto), con upsert
      idempotente — re-correr el pipeline el mismo día no duplica filas
- ✅ Feature Snapshot Store — insumos crudos congelados por predicción, y un
      punto único de cálculo (`model/predictor.py`) que recalcula predicciones
      históricas desde esos snapshots sin fuga de información
- ✅ Backtest walk-forward básico sobre los snapshots congelados
      (`tracking/backtest.py`)
- ✅ Brier Score del consenso de mercado como benchmark del modelo
- ✅ Generar reporte en consola + CSV
- ✅ Dashboard en Streamlit
- ✅ Segundo modelo en paralelo (Skellam sobre carreras proyectadas) — si
      coincide con el modelo heurístico, es una señal más fuerte; si discrepan,
      el reporte te lo marca para que decidas tú
- ✅ Tracking de resultados reales + Brier Score + Log Loss (el cimiento antes
      de cualquier Machine Learning)
- ✅ Expected Value (EV) — lo que de verdad importa en apuestas, no solo accuracy
- ✅ Tabla de apuestas reales separada de las predicciones, con liquidación
      automática, ROI real y Closing Line Value (CLV)
- ✅ Versionado ligero (`model_version` + `git_commit` en cada predicción)
- ✅ Tests de integración del orquestador + CI en GitHub Actions

## Registrar una apuesta real (opcional)

Predecir no es lo mismo que apostar. Cuando decidas que un edge vale la pena
apostarlo de verdad:

```python
from db.database import record_bet

record_bet({
    "game_pk": 717468,           # lo ves en el reporte de main.py
    "game_date": "2026-07-03",
    "market": "moneyline",
    "side": "home",               # o "away"
    "odds": -150,
    "model_prob": 0.65,
    "expected_value": 0.083,      # de model.edge.expected_value()
    "stake": 1.0,                 # en las unidades que uses tú
})
```

`track_results.py` liquida la apuesta automáticamente en cuanto el juego
termina, y `print_performance_report()` te muestra el ROI real acumulado —
separado de la accuracy de las predicciones, porque son cosas distintas
(puedes acertar el favorito y aun así tener EV negativo si la cuota era mala).

## Tracking de resultados (Fase 2)

`main.py` guarda cada predicción. `track_results.py` va después y:
1. Busca predicciones de los últimos 5 días sin resultado guardado
2. Trae el marcador final real de cada una (MLB Stats API)
3. Calcula **accuracy** (¿acertó al favorito?) y **Brier Score** (qué tan
   bien calibradas están las probabilidades, no solo si acertó el ganador)

```bash
python track_results.py
```

Corre esto **antes** de `main.py` cada día, para que los juegos de ayer ya
tengan resultado cuando calcules las métricas. El Brier Score es la métrica
que de verdad importa: 0.0 = perfecto, 0.25 = igual que no decir nada
("siempre digo 50%"), más de 0.25 = el modelo está peor que adivinar.

**Por qué esto va antes que Machine Learning:** un ensemble de XGBoost/LightGBM
necesita miles de resultados históricos para entrenar y validar. Sin este
módulo de tracking, no hay forma de saber si el modelo actual sirve, mucho
menos de entrenar uno más complejo. Cuando acumulemos varios meses de datos
aquí, ahí se evalúa si vale la pena dar el salto a ML.

## Instalación

```bash
git clone <tu-repo>
cd mlb_edge_analyzer
python -m venv venv
source venv/bin/activate   # En Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Uso

### 1. Correr el análisis diario por consola

```bash
python main.py
```

Esto imprime el reporte del día, lo guarda en `mlb_edge.db` (SQLite local)
y lo exporta a `reports/reporte_YYYYMMDD.csv`. Correrlo dos veces el mismo
día para el mismo juego actualiza el registro existente en vez de duplicarlo
(upsert por `game_pk` + `game_date`) — necesario para que el historial que
alimenta el Brier Score no se contamine si el proceso se reintenta.

### 2. Abrir el dashboard

```bash
streamlit run dashboard/app.py
```

Se abre en tu navegador en `http://localhost:8501`.

### 3. Correr las pruebas automatizadas

```bash
pytest
```

Prueba la lógica del modelo (probabilidad, edge, Skellam, proyección de
carreras) sin necesidad de red — son pruebas puras de matemáticas/lógica,
no dependen de la MLB Stats API.

`tests/test_main_pipeline.py` es distinto de los demás: es un test de
*integración* que mockea la capa de red y verifica que `main.analyze_today()`
efectivamente invoque el modelo real (no placeholders hardcodeados). Corre
automáticamente en cada push vía GitHub Actions (`.github/workflows/tests.yml`).

### 4. Revisar los logs

Cada corrida de `python main.py` escribe en `logs/mlb_edge_YYYYMMDD.log`
(un archivo por día) — ahí quedan registrados los pitchers/equipos para
los que falló alguna consulta a la API, con el motivo exacto. La consola
solo muestra advertencias/errores para no ensuciar el reporte.

## Cuotas de mercado — en vivo o manual

### Opción 1: The Odds API (recomendado)

```bash
export ODDS_API_KEY="tu-api-key-de-the-odds-api.com"
```

Con la variable de entorno seteada, `main.py` trae automáticamente las
cuotas moneyline de todos los bookmakers disponibles (región US), las
empareja contra los partidos del día por nombre de equipo, y usa **la
mejor cuota disponible** para calcular edge/EV. Además calcula el
**consenso sin vig** (promedio de la probabilidad "justa" de cada
bookmaker, sin el margen de la casa) — es la base para medir Closing Line
Value más adelante, no solo edge contra un mercado con vig incluido.

Si la key no está configurada, o la API falla, o un partido no aparece en
la respuesta: el pipeline no se rompe, simplemente no calcula edge para
ese partido (a menos que también tengas `MARKET_ODDS` cargado — ver abajo).

**Protección de presupuesto.** The Odds API es la API más limitada del
proyecto (free tier ~500 requests/mes, contra MLB Stats API y Open-Meteo
que no tienen ese techo). Por eso `data/odds_api.py`:
- Cachea la respuesta cruda por `ODDS_API_CACHE_TTL_SECONDS` (15 min por
  defecto) — refrescar el dashboard o correr `main.py` varias veces el
  mismo día no cuenta como llamadas nuevas mientras el caché siga vigente.
- Lleva la cuenta de llamadas reales del mes en `.cache/odds/` y deja de
  golpear la API si se alcanza `ODDS_API_MONTHLY_BUDGET` (500 por defecto),
  degradándose al último caché conocido (aunque esté vencido) antes que
  quedarse sin nada.

Ajustables por variable de entorno si tu plan es distinto:

```bash
export ODDS_API_CACHE_TTL_SECONDS=900
export ODDS_API_MONTHLY_BUDGET=500
```

### Opción 2: cuotas manuales (fallback, o si no tienes key todavía)

Edita `MARKET_ODDS` en `main.py` con el `game_pk` de cada partido (lo
puedes ver corriendo `python data/mlb_api.py` primero) y las cuotas
moneyline. Solo se usa si no hubo match en vivo para ese partido:

```python
MARKET_ODDS = {
    717468: {"away": -135, "home": +115},
}
```

### Run Line y Totales (solo manual por ahora)

Mismo patrón, en `MARKET_SPREADS` y `MARKET_TOTALS` dentro de `main.py`.
Arrancan en manual (no vía The Odds API en vivo) a propósito: pedir los
mercados `spreads`/`totals` además de `h2h` **triplicaría el consumo de
presupuesto de The Odds API por llamada** (cobra por mercado × región, no
por request). Conectarlos en vivo queda para cuando el flujo de picks esté
validado y el presupuesto lo permita.

```python
MARKET_SPREADS = {
    717468: {"line": 1.5, "home": -120, "away": +100},
}
MARKET_TOTALS = {
    717468: {"line": 8.5, "over": -110, "under": -110},
}
```

## Picks recomendados

Cada partido genera **entre 0 y 3 picks** (uno por mercado: moneyline, run
line, totales) según las cuotas que tengas cargadas — no hay obligación de
que exista un pick de moneyline específicamente, se evalúa el mejor mercado
disponible con datos.

Un candidato es viable si su EV supera `MIN_PICK_EV` (0.05 por defecto) **o**
su edge supera `MIN_PICK_EDGE` (0.04 por defecto). Si ningún mercado es
viable y `FORCE_AT_LEAST_ONE_PICK` está activo (por defecto sí), se genera
igual el menos malo, marcado `forced=True` — para que el reporte nunca deje
un partido sin recomendación, sin mentirte sobre cuáles picks tienen edge
real. Las métricas de desempeño (`tracking.results_tracker.compute_pick_performance`)
siempre separan picks reales de forzados — mezclarlos haría que un relleno
sin edge se vea como una falla del modelo real.

Los picks viven en su propia tabla (`Pick`, en `db/database.py`), separada
de `Bet`: un Pick es la recomendación del sistema en unidades **nocionales**
(1u pareja); un Bet es el dinero real que decidiste apostar. No todo Pick se
convierte en Bet, y liquidar Picks nunca toca el ROI real de Bet.

Se muestran en consola, en el dashboard, y en un CSV separado
(`reports/picks_YYYYMMDD.csv`, una fila por pick — no encajan en el CSV de
un partido por fila).

Ajustables por variable de entorno:

```bash
export MIN_PICK_EV=0.05
export MIN_PICK_EDGE=0.04
export FORCE_AT_LEAST_ONE_PICK=true
export MAX_PICKS_PER_GAME=3
```

## Favorito del mercado y candidatos a revisión

Cada partido con cuotas cargadas (en vivo o manuales) muestra explícitamente
quién favorece el mercado — usando el consenso **sin vig** cuando hay cuotas
en vivo de varios bookmakers (magnitud honesta, sin el margen de la casa), o
la probabilidad implícita de la cuota manual si es lo único disponible. Si
la diferencia entre los dos lados es menor a `pickem_threshold` (2 puntos
porcentuales por defecto), se marca como "pick'em" en vez de inventar un
favorito que el mercado no está dando.

Esto aparece en consola, CSV (`market_favorite_team`, `market_favorite_side`,
`market_favorite_prob`) y dashboard, junto con `model_edge_vs_market_favorite`
— el edge de tu modelo específicamente contra el lado que el mercado ya
favorece (distinto de `away_edge`/`home_edge`, que es el edge por lado).

Los partidos donde el edge supera `REVIEW_EDGE_THRESHOLD` (3 puntos
porcentuales por defecto) **y** los dos modelos independientes (heurístico y
Skellam) coinciden en el favorito se marcan con 🔎 como candidatos a
revisión — una preselección para que decidas tú, nunca una apuesta
automática. Ajustable con `REVIEW_EDGE_THRESHOLD`.

## Backtest walk-forward

```bash
python -m tracking.backtest
```

Recalcula predicciones desde los snapshots congelados en `feature_snapshots`
(nunca vuelve a golpear ninguna API) y mide Brier Score juego por juego,
ordenado por fecha — usa exactamente la misma lógica de `model/predictor.py`
que corre en vivo, así que no hay forma de que el backtest y el pipeline se
desincronicen. Con poca data acumulada se degrada explícitamente en vez de
aparentar una validación que no existe todavía.

## Feature Snapshot Store (recálculo histórico sin fuga de información)

Cada predicción guarda, además del resultado (`GameAnalysis`), los
**insumos crudos** que la generaron (ERA, OPS, bullpen, comando, descanso,
parque, clima, cuota de mercado) en la tabla `feature_snapshots`, con la
fecha exacta de captura.

Esto importa porque `data/stats.py` consulta stats de temporada *acumuladas
a hoy*, no *acumuladas a la fecha del juego*. Si en el futuro se mejora el
modelo y se quiere recalcular un juego de hace meses usando las funciones
de `data/stats.py` de nuevo, el ERA/OPS ya incluiría partidos posteriores
al juego que se recalcula — fuga de información hacia el pasado, el error
más grave que puede tener un backtest. Cualquier recálculo histórico debe
leer de `feature_snapshots` (vía `db.database.get_feature_snapshot`),
nunca volver a golpear la API en vivo.

## Closing Line Value (CLV)

`db.database.record_closing_odds(game_pk, side, closing_odds)` registra la
cuota de cierre de una apuesta ya hecha y calcula el CLV en espacio de
probabilidad. CLV positivo sostenido en el tiempo es el indicador que la
industria usa para separar skill real de varianza favorable en muestra
chica — más confiable que accuracy o incluso ROI a corto plazo.

Nota: capturar la cuota exacta al inicio del juego (no minutos u horas
antes) requiere un scheduler corriendo justo a esa hora — eso es trabajo
de un futuro Orchestration Engine, fuera del alcance actual. Por ahora,
`record_closing_odds` es una función que puedes llamar manualmente (o
disparar tú mismo cerca del inicio del juego) — el cálculo de CLV que hace
es correcto, la automatización de *cuándo* llamarla es lo pendiente.

### Script de captura

```bash
python scripts/capture_closing_lines.py
```

Busca las apuestas moneyline pendientes de hoy, las empareja contra las
cuotas en vivo, y llama `record_closing_odds` por ti. Córrelo cerca del
inicio de los juegos del día — a mano, o agendado en tu propio cron/Task
Scheduler apuntando al mismo `DATABASE_URL` que usa `main.py`.

**Deliberadamente no tiene un workflow de GitHub Actions asociado**: un
runner de Actions parte de un checkout limpio del repo en cada corrida y no
comparte tu `mlb_edge.db` local (SQLite está en `.gitignore`) — un cron ahí
encontraría 0 apuestas pendientes siempre, una automatización que aparenta
funcionar sin hacer nada. Si en algún momento el pipeline corre contra una
base de datos compartida (Postgres), ahí sí conviene moverlo a un Action
programado.

## Usar PostgreSQL en vez de SQLite

Por defecto todo corre en SQLite (`mlb_edge.db`, cero configuración).
Cuando quieras usar PostgreSQL:

```bash
export DATABASE_URL="postgresql://usuario:password@localhost:5432/mlb_edge"
python db/database.py   # crea las tablas
```

### Por qué esto importa si corres en GitHub Actions

`.github/workflows/daily_pipeline.yml` persiste `mlb_edge.db` entre corridas
vía `actions/cache` -- **best-effort**: GitHub puede liberar esa caché en
silencio (7+ días sin tocarla, o si se excede el límite de 10GB del repo).
Si eso pasa, el histórico completo (`game_analysis`/`picks`/
`feature_snapshots`) desaparece sin ningún error visible. Un Postgres
externo (con `DATABASE_URL` como GitHub secret) elimina ese riesgo por
completo — el workflow ya soporta esto (lee `secrets.DATABASE_URL` y salta
la caché de SQLite si existe), solo falta que exista la cuenta.

El esquema actual ya se verificó compatible con el dialecto PostgreSQL
(tipos de columna y DDL completo, ver `tests/test_database.py`) sin
necesidad de tocar código — falta únicamente la parte que solo el dueño
del proyecto puede hacer: crear la cuenta.

### Migrar a Postgres gratuito (Neon o Supabase), paso a paso

**Atajo recomendado si corres en GitHub Actions** (el caso real de este
repo: `mlb_edge.db` vive en la cache de `daily_pipeline.yml`, no en un
checkout local): después del paso 1 de abajo (crear la cuenta y agregar
el secret `DATABASE_URL`, paso 4), disparar
`.github/workflows/migrate_legacy_to_postgres.yml` a mano hace los pasos
2-3 automáticamente -- restaura esa misma cache y copia el histórico real
al Postgres nuevo en un solo workflow, sin necesitar un `mlb_edge.db`
local. Idempotente: si se corre dos veces por error, ninguna tabla ya
poblada en el destino se duplica.

1. **Crea la base de datos gratuita.**
   - [Neon](https://neon.tech): crea cuenta → "New Project" → copia el
     "Connection string" (ya viene con `?sslmode=require`).
   - [Supabase](https://supabase.com): crea cuenta → "New Project" →
     Settings → Database → "Connection string" (modo "URI").

   En ambos casos el string se ve así:
   ```
   postgresql://usuario:password@host:5432/nombre_db?sslmode=require
   ```

2. **Exporta los datos que ya tengas en SQLite** (si `mlb_edge.db` ya
   tiene historial que quieres conservar — si vas a empezar limpio, salta
   este paso y el siguiente, y ve directo al paso 4):

   ```bash
   python3 -c "
   from sqlalchemy import create_engine
   from sqlalchemy.orm import sessionmaker
   import db.database as database

   sqlite_engine = create_engine('sqlite:///mlb_edge.db')
   SqliteSession = sessionmaker(bind=sqlite_engine)
   session = SqliteSession()

   import json
   dump = {}
   for table in database.Base.metadata.sorted_tables:
       rows = session.execute(table.select()).mappings().all()
       dump[table.name] = [dict(r) for r in rows]
   with open('sqlite_export.json', 'w') as f:
       json.dump(dump, f, default=str)
   print({k: len(v) for k, v in dump.items()})
   "
   ```

   Esto genera `sqlite_export.json` con todas las filas de las 5 tablas.

3. **Importa a Postgres** (con `DATABASE_URL` ya apuntando a Neon/Supabase):

   ```bash
   export DATABASE_URL="postgresql://usuario:password@host:5432/nombre_db?sslmode=require"
   python3 -c "
   import json
   from datetime import datetime
   from sqlalchemy import DateTime
   import db.database as database

   database.Base.metadata.create_all(database.engine)  # crea las tablas en Postgres

   with open('sqlite_export.json') as f:
       dump = json.load(f)

   with database.engine.begin() as conn:
       for table in database.Base.metadata.sorted_tables:
           rows = dump.get(table.name, [])
           if not rows:
               continue
           # El JSON solo tiene strings -- las columnas DateTime necesitan
           # un objeto datetime real, no el string ISO que dejó el dump.
           datetime_cols = [c.name for c in table.columns if isinstance(c.type, DateTime)]
           for row in rows:
               for col in datetime_cols:
                   if isinstance(row.get(col), str):
                       row[col] = datetime.fromisoformat(row[col])
           conn.execute(table.insert(), rows)
   print('Importado:', {k: len(v) for k, v in dump.items()})
   "
   ```

4. **Configura el secret en GitHub** (para que `daily_pipeline.yml` lo use
   automáticamente): Settings → Secrets and variables → Actions → "New
   repository secret" → nombre `DATABASE_URL`, valor el connection string
   del paso 1. El workflow ya lo lee (`secrets.DATABASE_URL`) y salta por
   completo la caché de SQLite cuando este secret existe.

5. **Verifica localmente antes de confiar en la corrida automática:**

   ```bash
   export DATABASE_URL="postgresql://usuario:password@host:5432/nombre_db?sslmode=require"
   python main.py
   ```

   Si corre sin errores y ves tu histórico reflejado en `print_performance_report()`,
   la migración fue exitosa.

## Estructura del proyecto

```
mlb_edge_analyzer/
├── config.py              # configuración central
├── main.py                 # orquestador del análisis diario
├── data/
│   ├── mlb_api.py           # partidos + pitchers probables
│   └── stats.py              # ERA de pitchers, OPS de equipos/liga
├── model/
│   ├── probability.py        # modelo de probabilidad
│   └── edge.py                # implied prob, fair odds, edge, EV
├── db/
│   └── database.py             # persistencia (SQLite/PostgreSQL)
├── reports/
│   └── generate_report.py       # reporte en consola + CSV
└── dashboard/
    └── app.py                    # dashboard Streamlit
```

## Roadmap

Ya implementado (Fase 0 + 1 + 2 de la auditoría técnica):

- [x] Bullpen (ERA de relevo, ponderado por entradas lanzadas)
- [x] Clima del estadio (Open-Meteo, temperatura y viento)
- [x] Run Line y Totales (no solo Moneyline)
- [x] Conexión a API de cuotas en tiempo real (The Odds API), con no-vig y CLV
- [x] Tracking automático de resultados reales vs. predicción del modelo
- [x] Idempotencia (upsert), contrato de validación de esquema, CI
- [x] Feature Snapshot Store (recálculo histórico sin fuga de información)
- [x] Picks recomendados multi-mercado (Moneyline/Run Line/Totales), con
      picks forzados separados de los reales en las métricas de desempeño

Pendiente (deliberadamente fuera de alcance por ahora — mono-MLB primero):

- [ ] Risk Engine (límites de exposición agregada/correlación de portafolio)
- [ ] Model Registry versionado (más allá de `model_version` + `git_commit`)
- [ ] Backtesting Engine sobre picks históricos (hoy el backtest walk-forward
      solo recalcula probabilidades, no picks — ver tracking/backtest.py)
- [ ] Conectar Run Line/Totales en vivo vía The Odds API (spreads/totals),
      cuando el presupuesto mensual lo permita (hoy solo manual)
- [ ] Orchestration Engine (scheduler con reintentos/checkpoints, necesario
      para capturar la cuota de cierre real para CLV automáticamente)
- [ ] Regresión logística / shrinkage bayesiano reemplazando las constantes
      manuales de `model/probability.py`
- [ ] Despliegue del dashboard en un servidor (Streamlit Community Cloud
      es gratis para empezar, o Railway/Render ~$5-20/mes)
- [ ] Sport Adapter Layer / multi-deporte (NFL, NBA, NHL) — explícitamente
      pospuesto hasta que el pipeline de MLB esté maduro
