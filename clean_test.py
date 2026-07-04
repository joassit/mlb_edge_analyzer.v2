import sqlite3
conn = sqlite3.connect('mlb_edge.db')
cursor = conn.cursor()
cursor.execute('DELETE FROM game_analysis WHERE away_team = ?', ('Test',))
conn.commit()
conn.close()
print('Registro de prueba eliminado correctamente.')
