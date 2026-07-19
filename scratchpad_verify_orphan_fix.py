"""
Script temporal (corre DENTRO de un workflow de GitHub Actions, contra una
COPIA del mlb_edge.db real descargada del artifact de hoy) para validar en
vivo el fix de reconciliacion de juegos pospuestos antes de confiarlo a
producción.

Corre exactamente tracking.results_tracker.update_results() (la misma
funcion que main.py llama cada dia) y reporta:
- Cuantas filas se resolvieron (deben ser las 2 seguras: 823357 y 824414).
- Cuantas siguieron huerfanas a proposito por el guard anti-duplicado
  (deben ser las 2 con riesgo: 823062 y 823523).
- El winner/marcador guardado para las resueltas, para verificar que es
  el correcto.

No escribe nada a ningun lugar durable -- el mlb_edge.db de este runner es
una copia efimera del artifact, se descarta al terminar el job.
"""
import sys

sys.path.insert(0, ".")

from db.database import SessionLocal, GameAnalysis, ActualResult, init_db
from tracking.results_tracker import update_results

TARGET_ORPHANS = {
    823062: "esperado: sigue huerfano (duplicado -- 823035 ya predicho por separado)",
    823357: "esperado: se resuelve via game_pk=823356",
    824414: "esperado: se resuelve via game_pk=824412",
    823523: "esperado: sigue huerfano (duplicado -- 823524 ya predicho por separado)",
}

init_db()

updated = update_results(days_back=21)
print(f"update_results() actualizo {updated} fila(s).")

session = SessionLocal()
try:
    for pk, expectation in TARGET_ORPHANS.items():
        ar = session.query(ActualResult).filter(ActualResult.game_pk == pk).first()
        if ar:
            print(f"game_pk={pk}: RESUELTO -> {ar.away_score}-{ar.home_score} (winner={ar.winner}) | {expectation}")
        else:
            print(f"game_pk={pk}: sigue SIN resultado | {expectation}")
finally:
    session.close()
