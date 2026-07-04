import sqlite3
from datetime import datetime, timedelta
from data.mlb_api import get_game_result

def sync_results():
    conn = sqlite3.connect('mlb_edge.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Obtener juegos analizados
    cursor.execute("SELECT game_pk, game_date FROM game_analysis")
    games = cursor.fetchall()

    print(f"Sincronizando {len(games)} juegos...")

    for g in games:
        game_pk = g['game_pk']
        game_date = datetime.strptime(g['game_date'], "%Y-%m-%d")
        
        # 2. Obtener resultado de la API
        res = get_game_result(game_pk)
        
        if res and res.get('status') in ['Final', 'Game Over', 'Completed']:
            # Actualizar o insertar resultado
            cursor.execute("REPLACE INTO actual_results (game_pk, winner, total_runs) VALUES (?, ?, ?)",
                           (game_pk, res['winner'], res['total_runs']))
            print(f"✅ Sincronizado: {game_pk}")
            
        else:
            # 3. Lógica de limpieza: Si el juego tiene más de 3 días y no terminó, es un error/suspensión
            if datetime.now() > (game_date + timedelta(days=3)):
                print(f"🗑️ Limpiando juego estancado (más de 3 días): {game_pk}")
                # Opcional: Eliminar de game_analysis si no tiene resultado útil
                # cursor.execute("DELETE FROM game_analysis WHERE game_pk = ?", (game_pk,))

    conn.commit()
    conn.close()
    print("Sincronización y mantenimiento finalizados.")

if __name__ == "__main__":
    sync_results()