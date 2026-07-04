from db.database import fetch_all_analyses # Necesitarás crear esta función
from data.mlb_api import get_game_result

def audit():
    # 1. Traer todos los registros de tu DB
    records = fetch_all_analyses()
    
    for r in records:
        # 2. Consultar el resultado real
        real = get_game_result(r['game_pk'])
        if not real: continue
        
        # 3. Comparar
        predicted_winner = "home" if r['home_model_prob'] > r['away_model_prob'] else "away"
        actual_winner = real['winner']
        
        print(f"Juego {r['game_pk']}: Predicho {predicted_winner} | Real {actual_winner}")
        
        # 4. Calcular el error de las carreras proyectadas
        error = abs(r['home_proj_runs'] - real['home_score'])
        print(f"Error en carreras: {error}")

if __name__ == "__main__":
    audit()