import sqlite3

def audit_live():
    conn = sqlite3.connect('mlb_edge.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT a.*, r.winner, r.total_runs 
        FROM game_analysis a 
        LEFT JOIN actual_results r ON a.game_pk = r.game_pk
    """)
    data = cursor.fetchall()

    print(f"\n{'='*95}")
    print(f"{'AUDITORÍA DE PRECISIÓN (TOTALES)':^95}")
    print(f"{'='*95}")
    print(f"{'Juego':<30} | {'Pred':<6} | {'Real':<6} | {'Dif (Error)'}")
    print("-" * 95)
    
    total_mae = 0
    count = 0

    for d in data:
        matchup = f"{d['away_team']} @ {d['home_team']}"
        if d['total_runs'] is not None:
            pred = d['away_proj_runs'] + d['home_proj_runs']
            real = d['total_runs']
            diff = abs(pred - real)
            total_mae += diff
            count += 1
            print(f"{matchup:<30} | {pred:<6.2f} | {real:<6} | {diff:<10.2f}")
        else:
            print(f"{matchup:<30} | {'PENDIENTE':<6} | {'-':<6} | {'-'}")

    if count > 0:
        print("-" * 95)
        print(f"ERROR PROMEDIO (MAE): {total_mae/count:.2f} carreras")
    
    conn.close()

if __name__ == "__main__":
    audit_live()