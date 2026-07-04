import sqlite3
from datetime import date

def audit_today():
    today = date.today().strftime("%Y-%m-%d")
    conn = sqlite3.connect('mlb_edge.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Consultamos las proyecciones de hoy
    cursor.execute("SELECT * FROM game_analysis WHERE game_date = ?", (today,))
    projections = cursor.fetchall()

    if not projections:
        print("No hay análisis encontrados para hoy.")
        return

    print(f"{'Juego':<25} | {'Predicción':<10} | {'Resultado':<10} | {'¿Acierto?'}")
    print("-" * 65)

    for p in projections:
        # Buscamos el resultado real en la otra tabla
        cursor.execute("SELECT winner FROM actual_results WHERE game_pk = ?", (p['game_pk'],))
        real = cursor.fetchone()
        
        if real:
            pred = "HOME" if p['home_model_prob'] > p['away_model_prob'] else "AWAY"
            res = real['winner'].upper()
            acierto = "✅ SÍ" if pred == res else "❌ NO"
            
            print(f"{p['away_team']} @ {p['home_team']:<10} | {pred:<10} | {res:<10} | {acierto}")
        else:
            print(f"{p['away_team']} @ {p['home_team']:<10} | Pendiente de resultado...")

    conn.close()

if __name__ == "__main__":
    audit_today()