"""
Verifica si GET /teams/{id}/roster?rosterType=active&date=YYYY-MM-DD
de la MLB Stats API realmente devuelve un snapshot histórico distinto
por fecha, o si ignora el parámetro `date` y siempre devuelve el roster
vigente al momento de la llamada (lo cual invalidaria el fix propuesto
de "bajo costo" para bullpen_era_as_of).

Estrategia: pedir el roster de 3 equipos en 3 fechas bien separadas
dentro de 2024 (abril, julio, septiembre) y comparar el set de
person_id devuelto. Si el `date` param funciona, se espera churn real
(call-ups/IL/trades) entre esas fechas para casi cualquier equipo en una
temporada completa. Si las 3 respuestas son IDENTICAS para todos los
equipos, es evidencia fuerte de que el parametro se ignora.
"""
import json
import requests

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"
TEAM_IDS = {"Dodgers": 119, "Orioles": 110, "Padres": 135}
DATES = ["2024-04-15", "2024-07-15", "2024-09-15"]

out = {}
for team_name, team_id in TEAM_IDS.items():
    out[team_name] = {}
    for d in DATES:
        try:
            resp = requests.get(
                f"{MLB_API_BASE}/teams/{team_id}/roster",
                params={"rosterType": "active", "date": d},
                timeout=20,
            )
            resp.raise_for_status()
            roster = resp.json().get("roster", [])
            person_ids = sorted(p["person"]["id"] for p in roster)
            out[team_name][d] = {"n": len(person_ids), "person_ids": person_ids}
        except Exception as e:
            out[team_name][d] = {"error": str(e)}

# Comparación explícita
comparisons = {}
for team_name in TEAM_IDS:
    sets_by_date = {
        d: set(out[team_name][d].get("person_ids", []))
        for d in DATES if "person_ids" in out[team_name][d]
    }
    dates_list = list(sets_by_date.keys())
    identical_pairs = []
    for i in range(len(dates_list)):
        for j in range(i + 1, len(dates_list)):
            d1, d2 = dates_list[i], dates_list[j]
            identical_pairs.append({
                "dates": [d1, d2],
                "identical": sets_by_date[d1] == sets_by_date[d2],
                "symmetric_diff_count": len(sets_by_date[d1] ^ sets_by_date[d2]),
            })
    comparisons[team_name] = identical_pairs

result = {"rosters": out, "comparisons": comparisons}
print("===JSON_ROSTER_VERIFY_START===")
print(json.dumps(result, indent=2))
print("===JSON_ROSTER_VERIFY_END===")
