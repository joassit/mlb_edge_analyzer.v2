"""
TEMPORAL -- barrido k_ip_era x k_ip_ops sobre los 382 juegos reales de
mayo 2024 (ya ingeridos, artifact historical-backtest-db-2024-05), y
comparación de compresión de mu contra producción real (artifact del
daily_pipeline.yml más reciente). Ningún HTTP a MLB Stats API en este
script -- ambas DBs ya vienen descargadas por el paso anterior del
workflow via actions/download-artifact (servidor a servidor, sin pasar
por la red del sandbox de desarrollo).

LIMITACION IMPORTANTE, documentada explicitamente en vez de ocultarla:
HistoricalAnalysis NO persiste innings_pitched (solo el ERA crudo ya
point-in-time). Sin eso no se puede aplicar shrunk_era() con un k_ip
distinto de forma exacta. Para poder correr el barrido pedido SIN hacer
llamadas HTTP nuevas, se aproxima innings-pitched-acumuladas y
plate-appearances-acumuladas a partir de dias transcurridos desde el
inicio de temporada (2024-03-20) hasta as_of_date, con tasas tipicas de
MLB (ver IP_PROXY_RATE/PA_PROXY_RATE abajo). Esto es una aproximacion de
orden de magnitud, no el conteo real -- los valores exactos de k_ip
optimos podrian correr una vez que se persista IP/PA real.
"""

import sqlite3
import sys
from datetime import date

SEASON_START = date(2024, 3, 20)
LEAGUE_ERA = 4.30
LEAGUE_OPS = 0.750
STARTER_WEIGHT = 0.65
HOME_FIELD_RUNS_BONUS = 0.15

# Aproximacion (ver docstring): dias_transcurridos * tasa -> IP/PA acumuladas.
# starts_per_day = (162 juegos/temporada / 193 dias) / 5 (rotacion de 5) = 0.168
# IP_PROXY_RATE = starts_per_day * 5.2 IP/apertura (promedio MLB moderno)
IP_PROXY_RATE = 0.874
# games_per_day = 162/193 = 0.839 ; PA_PROXY_RATE = games_per_day * 38.5 PA/equipo/juego
PA_PROXY_RATE = 32.3


def shrink(raw_value: float, sample_proxy: float, league_value: float, k: float) -> float:
    if k == 0:
        return raw_value
    return (raw_value * sample_proxy + league_value * k) / (sample_proxy + k)


def offense_factor(ops: float, league_ops: float = LEAGUE_OPS, exponent: float = 1.8) -> float:
    return (ops / league_ops) ** exponent


def project_runs(ops: float, opp_era: float, opp_bullpen_era: float, is_home: bool) -> float:
    off = offense_factor(ops)
    opp_pitching = STARTER_WEIGHT * opp_era + (1 - STARTER_WEIGHT) * opp_bullpen_era
    pitching_factor = opp_pitching / LEAGUE_ERA
    runs = 4.4 * off * pitching_factor  # park_factor_weight=1.0, park_factor omitido (neutral para este barrido)
    if is_home:
        runs += HOME_FIELD_RUNS_BONUS
    return max(runs, 0.3)


def skellam_win_prob(mu_home: float, mu_away: float) -> float:
    # Reimplementado localmente (sin importar model/ para no arrastrar
    # dependencias de config.py de produccion en este script temporal) --
    # misma formula que model/skellam_model.py: P(Home - Away > 0).
    from scipy.stats import skellam
    return 1.0 - skellam.cdf(0, mu_home, mu_away)


def pearson(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)


def r_squared(pred, actual):
    n = len(pred)
    mean_actual = sum(actual) / n
    ss_res = sum((a - p) ** 2 for p, a in zip(pred, actual))
    ss_tot = sum((a - mean_actual) ** 2 for a in actual)
    if ss_tot == 0:
        return None
    return 1 - ss_res / ss_tot


def brier(probs, outcomes):
    n = len(probs)
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / n


def load_games(db_path: str, run_id: int = 1):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT a.game_pk, a.game_date, a.as_of_date, a.away_era, a.home_era,
               a.away_ops, a.home_ops, a.away_bullpen_era, a.home_bullpen_era,
               g.home_score, g.away_score, g.winner
        FROM historical_analysis a
        JOIN historical_game g ON g.game_pk = a.game_pk AND g.run_id = a.run_id
        WHERE a.run_id = ?
    """, (run_id,))
    rows = []
    for r in cur.fetchall():
        if r["winner"] is None or r["home_score"] is None or r["away_score"] is None:
            continue
        if None in (r["away_era"], r["home_era"], r["away_ops"], r["home_ops"],
                    r["away_bullpen_era"], r["home_bullpen_era"]):
            continue
        rows.append(dict(r))
    conn.close()
    return rows


def main() -> int:
    print("=" * 100)
    print("VERIFICACION DE SCHEMA -- confirmando si innings_pitched esta persistido")
    print("=" * 100)
    conn = sqlite3.connect("historical_backtest.db")
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(historical_analysis)")
    cols = [r[1] for r in cur.fetchall()]
    print("Columnas de historical_analysis:", cols)
    has_ip = "away_innings_pitched" in cols or "home_innings_pitched" in cols
    print(f"innings_pitched persistido: {has_ip}")
    conn.close()
    if not has_ip:
        print("CONFIRMADO: no hay innings_pitched real en la tabla -- se usa la aproximacion "
              "por dias-desde-inicio-de-temporada documentada en el docstring de este script.")

    rows = load_games("historical_backtest.db")
    n = len(rows)
    print(f"\nn={n} juegos cargados (mismo universo que las corridas anteriores)")

    print("\n" + "=" * 100)
    print("BARRIDO k_ip_era x k_ip_ops -- 16 combinaciones")
    print("=" * 100)
    header = f"{'k_ip_era':>9} {'k_ip_ops':>9} {'margen_min':>11} {'margen_max':>11} {'pearson':>9} {'r2':>9} {'brier_skellam':>14}"
    print(header)
    print("-" * len(header))

    results = []
    for k_ip_era in (20, 30, 40, 60):
        for k_ip_ops in (0, 200, 500, 1000):
            margins_proj, margins_real, home_probs, home_wins = [], [], [], []
            for r in rows:
                d_away_asof = (date.fromisoformat(r["as_of_date"]) - SEASON_START).days
                d_away_asof = max(d_away_asof, 1)
                ip_proxy = d_away_asof * IP_PROXY_RATE
                pa_proxy = d_away_asof * PA_PROXY_RATE

                away_era_shrunk = shrink(r["away_era"], ip_proxy, LEAGUE_ERA, k_ip_era)
                home_era_shrunk = shrink(r["home_era"], ip_proxy, LEAGUE_ERA, k_ip_era)
                away_ops_shrunk = shrink(r["away_ops"], pa_proxy, LEAGUE_OPS, k_ip_ops)
                home_ops_shrunk = shrink(r["home_ops"], pa_proxy, LEAGUE_OPS, k_ip_ops)

                away_mu = project_runs(away_ops_shrunk, home_era_shrunk, r["home_bullpen_era"], is_home=False)
                home_mu = project_runs(home_ops_shrunk, away_era_shrunk, r["away_bullpen_era"], is_home=True)

                margen_proy = home_mu - away_mu
                margen_real = r["home_score"] - r["away_score"]
                margins_proj.append(margen_proy)
                margins_real.append(margen_real)

                p_home = skellam_win_prob(home_mu, away_mu)
                home_probs.append(p_home)
                home_wins.append(1 if r["winner"] == "home" else 0)

            row_result = {
                "k_ip_era": k_ip_era, "k_ip_ops": k_ip_ops,
                "margen_min": min(margins_proj), "margen_max": max(margins_proj),
                "pearson": pearson(margins_proj, margins_real),
                "r2": r_squared(margins_proj, margins_real),
                "brier": brier(home_probs, home_wins),
            }
            results.append(row_result)
            print(f"{k_ip_era:>9} {k_ip_ops:>9} {row_result['margen_min']:>11.2f} {row_result['margen_max']:>11.2f} "
                  f"{row_result['pearson']:>9.4f} {row_result['r2']:>9.4f} {row_result['brier']:>14.4f}")

    best_pearson = max(results, key=lambda r: r["pearson"])
    best_brier = min(results, key=lambda r: r["brier"])
    print(f"\nMejor Pearson: k_ip_era={best_pearson['k_ip_era']} k_ip_ops={best_pearson['k_ip_ops']} -> pearson={best_pearson['pearson']:.4f}")
    print(f"Mejor Brier:   k_ip_era={best_brier['k_ip_era']} k_ip_ops={best_brier['k_ip_ops']} -> brier={best_brier['brier']:.4f}")

    print("\n" + "=" * 100)
    print("PRODUCCION REAL (mlb_edge.db del ultimo daily_pipeline.yml exitoso) vs. MAYO 2024")
    print("=" * 100)
    try:
        conn = sqlite3.connect("mlb_edge_production.db")
        cur = conn.cursor()
        cur.execute("SELECT game_pk, game_date, away_team, home_team, away_proj_runs, home_proj_runs FROM game_analysis "
                     "WHERE away_proj_runs IS NOT NULL AND home_proj_runs IS NOT NULL")
        prod_rows = cur.fetchall()
        conn.close()
        print(f"n={len(prod_rows)} juegos con proyeccion en produccion real")
        for pr in prod_rows:
            print(f"  game_pk={pr[0]} {pr[1]} {pr[2]}@{pr[3]}  away_mu={pr[4]:.2f}  home_mu={pr[5]:.2f}  margen={pr[5]-pr[4]:+.2f}")
        if prod_rows:
            prod_margins = [pr[5] - pr[4] for pr in prod_rows]
            print(f"\nRango de margen proyectado en PRODUCCION HOY: [{min(prod_margins):.2f}, {max(prod_margins):.2f}]")
        may_margins_k60_0 = [r["margen_max"] for r in results if r["k_ip_era"] == 60 and r["k_ip_ops"] == 0]
        may_row = next(r for r in results if r["k_ip_era"] == 60 and r["k_ip_ops"] == 0)
        print(f"Rango de margen proyectado en MAYO 2024 (k_ip_era=60,k_ip_ops=0, config actual de produccion): "
              f"[{may_row['margen_min']:.2f}, {may_row['margen_max']:.2f}]")
    except Exception as e:
        print(f"::error::No se pudo leer mlb_edge_production.db: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
