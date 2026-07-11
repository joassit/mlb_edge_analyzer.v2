"""
Script temporal -- corre DENTRO de un workflow de GitHub Actions para
consolidar en un solo historical_backtest.db las 4 temporadas de
historical_engine (2022-2025).

`dest` ya tiene los datos de 2023/2024 (recién ingeridos con el código
actual). Este script copia adentro las filas de cada `source` (artifacts
2022/2025 ya ingeridos con el mismo código corregido, descargados por el
workflow), remapeando el id de historical_run para no chocar con los que
ya existen en `dest` -- el resto de las tablas solo referencian
run_id/season_year (no hay foreign key real en SQLite acá), así que
remapear ese único id basta para mantener la trazabilidad de cada fila a
su corrida original.
"""
import sqlite3
import sys

CHILD_TABLES = [
    "historical_game", "historical_analysis", "historical_prediction",
    "historical_calibration", "historical_metrics", "historical_simulation",
]


def _columns(cur, table, alias=""):
    prefix = f"{alias}." if alias else ""
    cur.execute(f"PRAGMA {prefix}table_info({table})")
    return [row[1] for row in cur.fetchall()]


def merge_one(cur, alias):
    cur.execute("SELECT COALESCE(MAX(id), 0) FROM historical_run")
    offset = cur.fetchone()[0]

    run_cols = _columns(cur, "historical_run")
    id_idx = run_cols.index("id")
    cur.execute(f"SELECT {','.join(run_cols)} FROM {alias}.historical_run")
    id_map = {}
    for row in cur.fetchall():
        old_id = row[id_idx]
        new_id = offset + old_id
        id_map[old_id] = new_id
        new_row = list(row)
        new_row[id_idx] = new_id
        placeholders = ",".join("?" for _ in run_cols)
        cur.execute(f"INSERT INTO historical_run ({','.join(run_cols)}) VALUES ({placeholders})", new_row)

    for table in CHILD_TABLES:
        t_cols = _columns(cur, table, alias)
        if not t_cols:
            continue
        row_id_idx = t_cols.index("id")
        run_idx = t_cols.index("run_id")
        insert_cols = [c for c in t_cols if c != "id"]
        placeholders = ",".join("?" for _ in insert_cols)
        cur.execute(f"SELECT {','.join(t_cols)} FROM {alias}.{table}")
        for row in cur.fetchall():
            row = list(row)
            row[run_idx] = id_map.get(row[run_idx], row[run_idx])
            values = [v for j, v in enumerate(row) if j != row_id_idx]
            cur.execute(f"INSERT INTO {table} ({','.join(insert_cols)}) VALUES ({placeholders})", values)


def main(dest_path: str, source_paths: list[str]) -> None:
    conn = sqlite3.connect(dest_path)
    cur = conn.cursor()
    for i, src in enumerate(source_paths):
        alias = f"src{i}"
        cur.execute("ATTACH DATABASE ? AS " + alias, (src,))
        before = cur.execute("SELECT COUNT(*) FROM historical_game").fetchone()[0]
        merge_one(cur, alias)
        after = cur.execute("SELECT COUNT(*) FROM historical_game").fetchone()[0]
        conn.commit()
        cur.execute(f"DETACH DATABASE {alias}")
        print(f"fusionado {src}: +{after - before} filas en historical_game (total ahora {after})")
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
