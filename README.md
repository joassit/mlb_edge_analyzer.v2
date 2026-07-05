# MLB Edge Analyzer

Sistema propio para comparar la probabilidad de tu modelo contra el mercado
de apuestas de MLB. Construido por etapas, empezando 100% gratis.

## Etapa 1 (esta versión) — 100% gratuita

- ✅ Obtener partidos del día (MLB Stats API, oficial y gratuita)
- ✅ Obtener pitchers probables/confirmados
- ✅ Obtener ERA de pitchers y OPS de equipos (MLB Stats API)
- ✅ Calcular probabilidad del modelo (heurístico + Skellam), no placeholders
- ✅ Cuotas en vivo (The Odds API) con fallback a `MARKET_ODDS` manual
- ✅ Edge, EV y consenso sin vig (no-vig) contra el mercado
- ✅ Guardar histórico en base de datos (SQLite por defecto), con upsert
      idempotente — re-correr el pipeline el mismo día no duplica filas
- ✅ Feature Snapshot Store — insumos crudos congelados por predicción,
      para poder recalcular juegos históricos sin fuga de información
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

### Opción 2: cuotas manuales (fallback, o si no tienes key todavía)

Edita `MARKET_ODDS` en `main.py` con el `game_pk` de cada partido (lo
puedes ver corriendo `python data/mlb_api.py` primero) y las cuotas
moneyline. Solo se usa si no hubo match en vivo para ese partido:

```python
MARKET_ODDS = {
    717468: {"away": -135, "home": +115},
}
```

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

## Usar PostgreSQL en vez de SQLite

Por defecto todo corre en SQLite (`mlb_edge.db`, cero configuración).
Cuando quieras usar PostgreSQL:

```bash
export DATABASE_URL="postgresql://usuario:password@localhost:5432/mlb_edge"
python db/database.py   # crea las tablas
```

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
│   └── edge.py                # implied prob, fair odds, edge, Kelly
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

Pendiente (deliberadamente fuera de alcance por ahora — mono-MLB primero):

- [ ] Risk Engine (límites de exposición agregada/correlación de portafolio)
- [ ] Model Registry versionado (más allá de `model_version` + `git_commit`)
- [ ] Backtesting Engine sobre los snapshots ya congelados
- [ ] Orchestration Engine (scheduler con reintentos/checkpoints, necesario
      para capturar la cuota de cierre real para CLV automáticamente)
- [ ] Regresión logística / shrinkage bayesiano reemplazando las constantes
      manuales de `model/probability.py`
- [ ] Despliegue del dashboard en un servidor (Streamlit Community Cloud
      es gratis para empezar, o Railway/Render ~$5-20/mes)
- [ ] Sport Adapter Layer / multi-deporte (NFL, NBA, NHL) — explícitamente
      pospuesto hasta que el pipeline de MLB esté maduro
