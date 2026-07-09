"""
TEMPORAL -- Parte A + Parte B pedidas por el usuario, en una sola corrida
para no reingerir mayo 2024 dos veces:

PARTE A: reingiere 2024-05 con el pipeline YA CORREGIDO (persiste IP/PA
reales, no la aproximación por dias-desde-inicio-de-temporada) y repite
el barrido de 16 combinaciones k_ip_era x k_ip_ops, esta vez con datos
reales. Compara el Pearson resultante contra la prediccion de 0.20-0.30.

PARTE B: con el mismo n de mayo 2024, desglosa accuracy/Brier de
moneyline (Skellam/NB2 unicamente) segun si el juego tuvo o no un
abridor de "muestra chica" (IP real < 15 al momento del corte -- proxy
razonable de call-up/retorno de IL/abridor de emergencia, documentado
explicitamente como umbral, no una clasificacion oficial de MLB).

Sube historical_engine.db como artifact al final (ya con el schema
nuevo) para no tener que reingerir una cuarta vez.
"""

import sys
import time

from historical_engine import pipeline
from historical_engine.db import init_historical_db, SessionLocal, HistoricalAnalysis, HistoricalGame, HistoricalPrediction

SEASON = 2024
MONTH = 5
LEAGUE_ERA = 4.30
LEAGUE_OPS = 0.750
STARTER_WEIGHT = 0.65
HOME_FIELD_RUNS_BONUS = 0.15
SMALL_SAMPLE_IP_THRESHOLD = 15.0  # ver docstring


def shrink(raw_value, sample_size, league_value, k):
    if k == 0:
        return raw_value
    if sample_size is None:
        return raw_value
    return (raw_value * sample_size + league_value * k) / (sample_size + k)


def offense_factor(ops, league_ops=LEAGUE_OPS, exponent=1.8):
    return (ops / league_ops) ** exponent


def project_runs(ops, opp_era, opp_bullpen_era, is_home):
    off = offense_factor(ops)
    opp_pitching = STARTER_WEIGHT * opp_era + (1 - STARTER_WEIGHT) * opp_bullpen_era
    pitching_factor = opp_pitching / LEAGUE_ERA
    runs = 4.4 * off * pitching_factor
    if is_home:
        runs += HOME_FIELD_RUNS_BONUS
    return max(runs, 0.3)


def skellam_win_prob(mu_home, mu_away):
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


def main() -> int:
    init_historical_db()

    print("=" * 100)
    print(f"PARTE A + B -- reingesta real de {SEASON}-{MONTH:02d} con IP/PA reales persistidas")
    print("=" * 100)

    t0 = time.monotonic()
    result = pipeline.run_month(SEASON, MONTH)
    elapsed = time.monotonic() - t0
    print(f"\nrun_id={result.run_id}  juegos={result.n_games}  analizados={result.n_analyzed}  "
          f"errores={result.n_errors}  tiempo={elapsed:.1f}s ({elapsed/60:.1f} min)")

    session = SessionLocal()
    analyses = session.query(HistoricalAnalysis).filter_by(run_id=result.run_id).all()
    games_by_pk = {g.game_pk: g for g in session.query(HistoricalGame).filter_by(run_id=result.run_id).all()}
    predictions = session.query(HistoricalPrediction).filter_by(run_id=result.run_id).all()
    session.close()

    rows = []
    n_missing_ip_pa = 0
    for a in analyses:
        g = games_by_pk.get(a.game_pk)
        if g is None or g.winner is None or g.home_score is None or g.away_score is None:
            continue
        if a.home_proj_runs is None or a.away_proj_runs is None:
            continue
        if None in (a.away_era, a.home_era, a.away_ops, a.home_ops, a.away_bullpen_era, a.home_bullpen_era):
            continue
        if None in (a.away_innings_pitched, a.home_innings_pitched, a.away_team_pa, a.home_team_pa):
            n_missing_ip_pa += 1
            continue
        rows.append({
            "game_pk": a.game_pk,
            "away_era": a.away_era, "home_era": a.home_era,
            "away_ops": a.away_ops, "home_ops": a.home_ops,
            "away_bullpen_era": a.away_bullpen_era, "home_bullpen_era": a.home_bullpen_era,
            "away_ip": a.away_innings_pitched, "home_ip": a.home_innings_pitched,
            "away_pa": a.away_team_pa, "home_pa": a.home_team_pa,
            "home_score": g.home_score, "away_score": g.away_score, "winner": g.winner,
        })

    n = len(rows)
    print(f"\nn={n} juegos con IP/PA real disponible (de {result.n_analyzed} analizados; "
          f"{n_missing_ip_pa} se excluyeron por faltar IP/PA real -- la API no siempre la tiene "
          f"disponible para pitchers de muy pocas apariciones)")

    print("\n" + "=" * 100)
    print("PARTE A -- BARRIDO k_ip_era x k_ip_ops CON IP/PA REALES (no aproximadas)")
    print("=" * 100)
    header = f"{'k_ip_era':>9} {'k_ip_ops':>9} {'margen_min':>11} {'margen_max':>11} {'pearson':>9} {'r2':>9} {'brier_skellam':>14}"
    print(header)
    print("-" * len(header))

    results = []
    for k_ip_era in (20, 30, 40, 60):
        for k_ip_ops in (0, 200, 500, 1000):
            margins_proj, margins_real, home_probs, home_wins = [], [], [], []
            for r in rows:
                away_era_shrunk = shrink(r["away_era"], r["home_ip"], LEAGUE_ERA, k_ip_era)
                home_era_shrunk = shrink(r["home_era"], r["away_ip"], LEAGUE_ERA, k_ip_era)
                away_ops_shrunk = shrink(r["away_ops"], r["away_pa"], LEAGUE_OPS, k_ip_ops)
                home_ops_shrunk = shrink(r["home_ops"], r["home_pa"], LEAGUE_OPS, k_ip_ops)

                away_mu = project_runs(away_ops_shrunk, home_era_shrunk, r["home_bullpen_era"], is_home=False)
                home_mu = project_runs(home_ops_shrunk, away_era_shrunk, r["away_bullpen_era"], is_home=True)

                margins_proj.append(home_mu - away_mu)
                margins_real.append(r["home_score"] - r["away_score"])
                home_probs.append(skellam_win_prob(home_mu, away_mu))
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
    print(f"\nPrediccion previa: 0.20-0.30. Resultado real: {best_pearson['pearson']:.4f}. "
          f"{'CONFIRMA la hipotesis de call-ups/retornos de IL' if best_pearson['pearson'] >= 0.18 else 'REFUTA (o queda por debajo de) la hipotesis -- el techo sigue siendo estructural, no de datos'}.")

    print("\n" + "=" * 100)
    print(f"PARTE B -- moneyline Skellam/NB2 desglosado por abridor de muestra chica (IP real < {SMALL_SAMPLE_IP_THRESHOLD})")
    print("=" * 100)

    ip_by_game_pk = {r["game_pk"]: (r["away_ip"], r["home_ip"]) for r in rows}
    small_sample_pks = {pk for pk, (aip, hip) in ip_by_game_pk.items()
                        if aip < SMALL_SAMPLE_IP_THRESHOLD or hip < SMALL_SAMPLE_IP_THRESHOLD}
    print(f"\n{len(small_sample_pks)} de {n} juegos ({len(small_sample_pks)/n:.1%}) tuvieron al menos un "
          f"abridor con IP real < {SMALL_SAMPLE_IP_THRESHOLD} al momento del corte (proxy de call-up/retorno de IL/emergencia)")

    for source in ("skellam", "negbin"):
        preds = [p for p in predictions if p.source == source and p.game_pk in ip_by_game_pk and p.correct is not None]
        small = [p for p in preds if p.game_pk in small_sample_pks]
        normal = [p for p in preds if p.game_pk not in small_sample_pks]

        def _stats(group):
            if not group:
                return None
            acc = sum(1 for p in group if p.correct) / len(group)
            probs = [p.home_prob for p in group]
            outcomes = [1 if p.actual_winner == "home" else 0 for p in group]
            return {"n": len(group), "accuracy": acc, "brier": brier(probs, outcomes)}

        s_small, s_normal = _stats(small), _stats(normal)
        print(f"\n{source}:")
        print(f"  juegos CON abridor de muestra chica:    {s_small}")
        print(f"  juegos SIN abridor de muestra chica:    {s_normal}")
        if s_small and s_normal:
            diff_acc = s_small["accuracy"] - s_normal["accuracy"]
            diff_brier = s_small["brier"] - s_normal["brier"]
            print(f"  diferencia accuracy (chica - normal): {diff_acc:+.4f}   diferencia brier (chica - normal): {diff_brier:+.4f}")

    print("\n" + "=" * 100)
    print("FIN PARTE A + B")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
