"""
TEMPORAL -- reconcilia la contradicción encontrada en la corrida real de
mayo 2024 (Pearson≈0 en margen de carreras, pero accuracy de moneyline
58.4%, por encima del azar). 3 chequeos pedidos por el usuario, todos
sobre los mismos datos ya analizados (nada nuevo de red más allá de
reingerir el mes, que no se persistió la vez pasada):

  1. Baseline ingenuo "siempre elegir al local" sobre los mismos 382
     juegos -- si da un número cercano al 58.4% de Skellam/NB2, el
     modelo no aporta nada más allá del sesgo de local.
  2. Spot-check manual de 5-10 juegos al azar: mu_home, mu_away, margen
     proyectado, marcador real, margen real -- confirma que el signo de
     la resta es consistente (un bug de signo invertido en un lado
     produciría exactamente un Pearson≈0 aunque el modelo tuviera señal).
  3. % de los 382 juegos donde el modelo proyectó al local como
     favorito (mu_home > mu_away).

Esta vez SÍ se persiste historical_engine.db (sqlite, ver
historical_engine/config.py::HISTORICAL_DATABASE_URL) como artifact de
Actions al final del job, para que un chequeo adicional futuro no
requiera reingerir por tercera vez.
"""

import random
import sys

from historical_engine import pipeline
from historical_engine.db import init_historical_db, SessionLocal, HistoricalGame, HistoricalAnalysis, HistoricalPrediction

SEASON = 2024
MONTH = 5


def main() -> int:
    init_historical_db()

    print("=" * 100)
    print(f"RECONCILIACION -- Pearson≈0 en margen vs. accuracy 58.4%% de moneyline ({SEASON}-{MONTH:02d})")
    print("=" * 100)

    result = pipeline.run_month(SEASON, MONTH)
    print(f"\nrun_id={result.run_id}  juegos={result.n_games}  analizados={result.n_analyzed}  errores={result.n_errors}")

    session = SessionLocal()
    try:
        games_by_pk = {g.game_pk: g for g in session.query(HistoricalGame).filter_by(run_id=result.run_id).all()}
        analyses = session.query(HistoricalAnalysis).filter_by(run_id=result.run_id).all()

        # Mismo universo que validate_all_sources()/analyze_runs_projection(): juegos
        # con resultado real conocido Y proyección de carreras disponible.
        rows = []
        for a in analyses:
            g = games_by_pk.get(a.game_pk)
            if g is None or g.winner is None or g.home_score is None or g.away_score is None:
                continue
            if a.home_proj_runs is None or a.away_proj_runs is None:
                continue
            rows.append({
                "game_pk": a.game_pk, "game_date": a.game_date,
                "away_team": g.away_team, "home_team": g.home_team,
                "mu_home": a.home_proj_runs, "mu_away": a.away_proj_runs,
                "home_score": g.home_score, "away_score": g.away_score,
                "winner": g.winner,
            })

        n = len(rows)
        print(f"\nn={n} juegos con proyeccion de carreras + resultado real (mismo universo que validate_all_sources/analyze_runs_projection)")

        print("\n--- CHEQUEO 1 -- BASELINE INGENUO: 'siempre elegir al local' ---")
        home_wins = sum(1 for r in rows if r["winner"] == "home")
        naive_accuracy = home_wins / n if n else None
        print(f"Locales ganaron {home_wins}/{n} = {naive_accuracy:.4f} ({naive_accuracy:.1%})")
        print("Comparar contra: heuristic=0.5445  skellam=0.5838  negbin=0.5838 (de la corrida anterior)")
        if naive_accuracy is not None:
            diff_skellam = 0.5838 - naive_accuracy
            print(f"Skellam/NB2 vs. baseline ingenuo: {diff_skellam:+.4f} ({diff_skellam:+.1%} puntos porcentuales)")
            if abs(diff_skellam) < 0.03:
                print("::warning::Skellam/NB2 esta a menos de 3pp del baseline ingenuo -- señal debil o nula mas alla del sesgo de local.")

        print("\n--- CHEQUEO 2 -- SPOT-CHECK MANUAL (5-10 juegos al azar, verificacion de signo) ---")
        random.seed(42)
        sample = random.sample(rows, min(10, n))
        header = f"{'fecha':<12} {'visitante':<22} {'local':<22} {'mu_away':>8} {'mu_home':>8} {'margen_proy':>12} {'marcador_real':>15} {'margen_real':>12} {'signo_ok':>9}"
        print(header)
        print("-" * len(header))
        sign_mismatches = 0
        for r in sample:
            margen_proy = r["mu_home"] - r["mu_away"]
            margen_real = r["home_score"] - r["away_score"]
            signo_ok = (margen_proy > 0) == (margen_real > 0) if margen_proy != 0 and margen_real != 0 else None
            marcador = f"{r['away_score']}-{r['home_score']}"
            print(f"{r['game_date']:<12} {r['away_team']:<22} {r['home_team']:<22} {r['mu_away']:>8.2f} {r['mu_home']:>8.2f} "
                  f"{margen_proy:>12.2f} {marcador:>15} {margen_real:>12} {str(signo_ok):>9}")
            if signo_ok is False:
                sign_mismatches += 1
        print(f"\nDe {len(sample)} juegos de muestra, {sign_mismatches} tuvieron signo del margen proyectado "
              f"opuesto al signo del margen real (esto es NORMAL en algunos casos individuales -- lo que "
              f"importaria es si pasa sistematicamente en TODOS o CASI TODOS, lo cual invertiria la correlacion).")

        # Chequeo de signo sobre TODO el universo (no solo la muestra de 10) --
        # cuenta cuantos SI coinciden en signo, para ver la tasa real.
        both_nonzero = [r for r in rows if (r["mu_home"] - r["mu_away"]) != 0 and (r["home_score"] - r["away_score"]) != 0]
        matches = sum(1 for r in both_nonzero if ((r["mu_home"] - r["mu_away"]) > 0) == ((r["home_score"] - r["away_score"]) > 0))
        print(f"\nSobre TODO el universo (n={len(both_nonzero)}, excluyendo empates de mu o de marcador): "
              f"el signo del margen proyectado coincidio con el del margen real en {matches}/{len(both_nonzero)} "
              f"= {matches/len(both_nonzero):.1%} de los juegos.")
        print("(si esto fuera ~50%% o menos, es evidencia fuerte de un bug de signo invertido en un lado; "
              "si esta bien por encima de 50%% pero el Pearson global da ~0, el problema es mas de MAGNITUD "
              "que de signo -- el modelo acierta la direccion pero no la escala del margen)")

        print("\n--- CHEQUEO 3 -- %% DE JUEGOS DONDE EL MODELO FAVORECIO AL LOCAL (mu_home > mu_away) ---")
        home_favored = sum(1 for r in rows if r["mu_home"] > r["mu_away"])
        away_favored = sum(1 for r in rows if r["mu_away"] > r["mu_home"])
        tied = n - home_favored - away_favored
        print(f"mu_home > mu_away (local favorito): {home_favored}/{n} = {home_favored/n:.1%}")
        print(f"mu_away > mu_home (visitante favorito): {away_favored}/{n} = {away_favored/n:.1%}")
        print(f"mu_home == mu_away (empate exacto): {tied}/{n} = {tied/n:.1%}")
        if home_favored / n > 0.70:
            print(f"::warning::El modelo favorece al local en mas del 70%% de los juegos ({home_favored/n:.1%}) -- "
                  f"esto limita cuanto puede correlacionar con el margen real si los visitantes ganan seguido en la muestra.")
        away_win_rate_in_sample = 1 - (home_wins / n)
        print(f"(para contexto: los visitantes ganaron el {away_win_rate_in_sample:.1%} de los juegos reales de esta muestra)")

    finally:
        session.close()

    print("\n" + "=" * 100)
    print("FIN RECONCILIACION")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
