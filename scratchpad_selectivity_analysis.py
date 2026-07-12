"""
Análisis de selectividad: ¿qué umbral de confianza históricamente rinde
>= 70% de acierto, y cuántos picks deja por semana?

Usa HistoricalPrediction de las 4 temporadas consolidadas (2022-2025).
confidence = max(home_prob, 1 - home_prob) -- la confianza declarada en el
favorito, mismo criterio que validation.py::_confidences_and_hits.
"""

import json
import sqlite3
from collections import defaultdict

conn = sqlite3.connect("historical_backtest.db")
cur = conn.cursor()

rows = cur.execute("""
    SELECT source, season_year, home_prob, correct
    FROM historical_prediction
    WHERE correct IS NOT NULL
""").fetchall()
conn.close()

# Semanas reales aproximadas de temporada regular MLB: ~26 semanas.
WEEKS_PER_SEASON = 26
N_SEASONS = 4

THRESHOLDS = [0.50, 0.55, 0.575, 0.60, 0.625, 0.65, 0.675, 0.70, 0.725, 0.75, 0.80]

by_source = defaultdict(list)
for source, season_year, home_prob, correct in rows:
    confidence = max(home_prob, 1.0 - home_prob)
    by_source[source].append((season_year, confidence, bool(correct)))

result = {}
for source, preds in by_source.items():
    total_n = len(preds)
    thresholds_out = []
    for t in THRESHOLDS:
        subset = [(sy, c, hit) for sy, c, hit in preds if c >= t]
        n = len(subset)
        if n == 0:
            thresholds_out.append({"threshold": t, "n": 0})
            continue
        hits = sum(1 for _, _, hit in subset if hit)
        by_season = defaultdict(lambda: [0, 0])
        for sy, _, hit in subset:
            by_season[sy][0] += 1
            by_season[sy][1] += 1 if hit else 0
        thresholds_out.append({
            "threshold": t,
            "n": n,
            "hit_rate": hits / n,
            "picks_per_week": n / (WEEKS_PER_SEASON * N_SEASONS),
            "pct_of_all_games": n / total_n,
            "by_season": {
                str(sy): {"n": v[0], "hit_rate": (v[1] / v[0]) if v[0] else None}
                for sy, v in sorted(by_season.items())
            },
        })
    result[source] = {"total_n": total_n, "thresholds": thresholds_out}

print("===JSON_SELECTIVITY_START===")
print(json.dumps(result, indent=2))
print("===JSON_SELECTIVITY_END===")
