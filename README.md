# MLB Edge Analyzer

Sistema propio para comparar la probabilidad de tu modelo contra el mercado
de apuestas de MLB. Construido por etapas, empezando 100% gratis.

## Etapa 1 (esta versión) — 100% gratuita

- ✅ Obtener partidos del día (MLB Stats API, oficial y gratuita)
- ✅ Obtener pitchers probables/confirmados
- ✅ Obtener ERA de pitchers y OPS de equipos (MLB Stats API)
- ✅ Calcular probabilidad del modelo
- ✅ Calcular edge (si cargas cuotas manualmente en `main.py`)
- ✅ Guardar histórico en base de datos (SQLite por defecto)
- ✅ Generar reporte en consola + CSV
- ✅ Dashboard en Streamlit
- ✅ Segundo modelo en paralelo (Skellam sobre carreras proyectadas) — si
      coincide con el modelo heurístico, es una señal más fuerte; si discrepan,
      el reporte te lo marca para que decidas tú
- ✅ Tracking de resultados reales + Brier Score + Log Loss (el cimiento antes
      de cualquier Machine Learning)
- ✅ Expected Value (EV) — lo que de verdad importa en apuestas, no solo accuracy
- ✅ Tabla de apuestas reales separada de las predicciones, con liquidación
      automática y cálculo de ROI real
- ✅ Versionado ligero (`model_version` + `git_commit` en cada predicción)

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

## Cargar cuotas de mercado (opcional, para ver el edge real)

Mientras no tengas una API de odds conectada, edita `MARKET_ODDS` en
`main.py` con el `game_pk` de cada partido (lo puedes ver corriendo
`python data/mlb_api.py` primero) y las cuotas moneyline:

```python
MARKET_ODDS = {
    717468: {"away": -135, "home": +115},
}
```

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

## Roadmap (Etapa 2 en adelante)

Funciones planeadas para cuando el modelo ya sea consistente:

- [ ] Bullpen (ERA de relevo, uso reciente de brazos)
- [ ] Clima del estadio (afecta home runs, sobre todo en parques altos)
- [ ] Run Line y Totales (no solo Moneyline)
- [ ] Conexión a API de cuotas en tiempo real (The Odds API u otra)
- [ ] Tracking automático de resultados reales vs. predicción del modelo
- [ ] Despliegue del dashboard en un servidor (Streamlit Community Cloud
      es gratis para empezar, o Railway/Render ~$5-20/mes)
