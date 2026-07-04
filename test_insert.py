import sqlite3
conn = sqlite3.connect('mlb_edge.db')
cursor = conn.cursor()
try:
    cursor.execute('INSERT INTO game_analysis (game_pk, away_team, home_team, game_date, away_proj_runs, home_proj_runs, home_model_prob, away_model_prob) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', 
                   (12345, 'Test', 'Test', '2026-07-03', 0, 0, 0, 0))
    conn.commit()
    print('Insertado correctamente')
except Exception as e:
    print(f'Error: {e}')
conn.close()
