import sqlite3
from data.mlb_api import get_game_result

def run_backtest():
    conn = sqlite3.connect('mlb_edge.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Traer todos los juegos analizados de la base de datos
    cursor.execute("SELECT * FROM game_analysis")
    all_analysis = cursor.fetchall()
    
    total_error = 0
    count = 0

    print(f"{'Juego':<25} | {'Pred. Runs':<10} | {'Real Runs':<10} | {'Error'}")
    print("-" * 60)

    for a in all_analysis:
        # 2. Obtener el resultado real de la API
        real = get_game_result(a['game_pk'])
        
        if real:
            # Calcular error (diferencia entre carreras totales proyectadas y reales)
            proj_total = a['away_proj_runs'] + a['home_proj_runs']
            real_total = real['total_runs']
            error = abs(proj_total - real_total)
            
            total_error += error
            count += 1
            
            print(f"{a['away_team']} @ {a['home_team']:<10} | {proj_total:<10.1f} | {real_total:<10} | {error:.1f}")

    if count > 0:
        print("-" * 60)
        print(f"Error Promedio (MAE): {total_error / count:.2f} carreras")
    else:
        print("No se encontraron resultados reales para comparar.")

    conn.close()

if __name__ == "__main__":
    run_backtest()