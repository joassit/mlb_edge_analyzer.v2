"""
MLB EDGE ANALYZER — Versión 0.6.0
Orquestador: Realiza auditoría del día anterior y proyecciones del día actual.
"""

from datetime import date
from logging_config import setup_logging
from data.mlb_api import get_schedule
from data.stats import (
    get_pitcher_era, get_team_ops, get_league_ops, get_bullpen_era,
    get_pitcher_command, get_pitcher_rest,
)
from data.park_factors import get_park_info
from data.weather import preload_weather
from model.runs_projection import LEAGUE_AVG_ERA
from model.predictor import predict_from_raw_inputs
from model.edge import implied_prob, edge, expected_value, market_favorite, no_vig_probs
from model.picks import generate_pick_candidates, select_picks_for_game
from data.odds_api import fetch_moneyline_odds, match_odds_to_game, consensus_no_vig_prob, best_available_price
from db.database import init_db, save_analysis, save_feature_snapshot, save_picks
from reports.generate_report import print_report, export_csv, export_picks_csv
from audit_live import audit_live
from tracking.results_tracker import update_results, print_performance_report
from config import (
    STARTER_WEIGHT, HOME_FIELD_ADVANTAGE, MODEL_VERSION, REVIEW_EDGE_THRESHOLD,
    MIN_PICK_EV, MIN_PICK_EDGE, FORCE_AT_LEAST_ONE_PICK, MAX_PICKS_PER_GAME,
)
from version_info import get_git_commit

# Cuotas de mercado cargadas a mano — se usan solo si no hay match en vivo
# de The Odds API (ODDS_API_KEY sin configurar, o el juego no aparece en la
# respuesta). Llena esto con el game_pk de cada partido (lo ves corriendo
# `python data/mlb_api.py`) y las cuotas moneyline reales.
MARKET_ODDS = {
    # 717468: {"away": -135, "home": +115},
}

# Run Line y Totales arrancan solo con cuotas manuales — The Odds API cobra
# presupuesto por mercado × región, así que pedir spreads/totals en vivo
# triplicaría el consumo de ODDS_API_MONTHLY_BUDGET por llamada. Se conecta
# en vivo más adelante si el presupuesto lo permite.
MARKET_SPREADS = {
    # 717468: {"line": 1.5, "home": -120, "away": +100},
}
MARKET_TOTALS = {
    # 717468: {"line": 8.5, "over": -110, "under": -110},
}


def analyze_today() -> list[dict]:
    league_ops = get_league_ops()
    games = get_schedule(date.today())
    weather_by_team = preload_weather(games, get_park_info)
    odds_events = fetch_moneyline_odds()
    results = []

    for g in games:
        if g.get("abstract_state") not in ["Preview", "Final"]:
            continue

        if not g.get("away_pitcher_id") or not g.get("home_pitcher_id"):
            continue

        away_era = get_pitcher_era(g["away_pitcher_id"])
        home_era = get_pitcher_era(g["home_pitcher_id"])
        away_ops = get_team_ops(g["away_team_id"])
        home_ops = get_team_ops(g["home_team_id"])

        if None in (away_era, home_era, away_ops, home_ops):
            continue

        away_bullpen = get_bullpen_era(g["away_team_id"])
        home_bullpen = get_bullpen_era(g["home_team_id"])
        away_cmd = get_pitcher_command(g["away_pitcher_id"])
        home_cmd = get_pitcher_command(g["home_pitcher_id"])
        away_rest = get_pitcher_rest(g["away_pitcher_id"])
        home_rest = get_pitcher_rest(g["home_pitcher_id"])
        park = get_park_info(g["home_team_id"])
        weather = weather_by_team.get(g["home_team_id"], {"temp_f": None})

        # Cuotas: primero se intenta la mejor disponible en vivo (The Odds
        # API); si no hay match para este juego, cae a MARKET_ODDS manual.
        odds_event = match_odds_to_game(odds_events, g["away_team"], g["home_team"])
        live_price = best_available_price(odds_event) if odds_event else None
        no_vig = consensus_no_vig_prob(odds_event) if odds_event else None
        price = live_price or MARKET_ODDS.get(g["game_pk"])

        # Run Line y Totales: solo cuotas manuales por ahora (ver
        # MARKET_SPREADS/MARKET_TOTALS arriba) — se calcula su no-vig igual
        # que en moneyline, cuando ambos lados están cargados.
        manual_rl = MARKET_SPREADS.get(g["game_pk"])
        if manual_rl and manual_rl.get("home") is not None and manual_rl.get("away") is not None:
            rl_home_novig, rl_away_novig = no_vig_probs(manual_rl["home"], manual_rl["away"])
        else:
            rl_home_novig = rl_away_novig = None

        manual_totals = MARKET_TOTALS.get(g["game_pk"])
        if manual_totals and manual_totals.get("over") is not None and manual_totals.get("under") is not None:
            totals_over_novig, totals_under_novig = no_vig_probs(manual_totals["over"], manual_totals["under"])
        else:
            totals_over_novig = totals_under_novig = None

        # Insumos crudos, congelados en el Feature Snapshot Store — el
        # mismo dict alimenta model.predictor.predict_from_raw_inputs()
        # hoy en vivo, y a cualquier recálculo histórico futuro que lea
        # este snapshot en vez de volver a golpear ninguna API. Las cuotas
        # de RL/Totales también se congelan aquí: los odds APIs gratuitos
        # no dan cuotas históricas, así que este es el único momento en
        # que se pueden capturar para un backtest futuro de picks.
        raw_inputs = {
            "away_era": away_era, "home_era": home_era,
            "away_ops": away_ops, "home_ops": home_ops, "league_ops": league_ops,
            "league_era": LEAGUE_AVG_ERA,
            "away_bullpen_era": away_bullpen, "home_bullpen_era": home_bullpen,
            "away_k_pct": away_cmd["k_pct"], "away_bb_pct": away_cmd["bb_pct"],
            "home_k_pct": home_cmd["k_pct"], "home_bb_pct": home_cmd["bb_pct"],
            "away_days_rest": away_rest["days_rest"], "away_last_outing_pitches": away_rest["last_outing_pitches"],
            "home_days_rest": home_rest["days_rest"], "home_last_outing_pitches": home_rest["last_outing_pitches"],
            "park_factor": park["park_factor"], "park_name": park["name"],
            "temp_f": weather.get("temp_f"), "wind_mph": weather.get("wind_mph"),
            "wind_direction_deg": weather.get("wind_direction_deg"),
            "market_price": price, "market_no_vig": no_vig,
            "market_run_line": manual_rl, "market_totals": manual_totals,
            "starter_weight": STARTER_WEIGHT, "home_field_advantage": HOME_FIELD_ADVANTAGE,
        }

        prediction = predict_from_raw_inputs(raw_inputs)
        away_mu, home_mu = prediction["away_proj_runs"], prediction["home_proj_runs"]
        away_model_prob, home_model_prob = prediction["away_model_prob"], prediction["home_model_prob"]
        away_skellam_prob, home_skellam_prob = prediction["away_skellam_prob"], prediction["home_skellam_prob"]
        home_covers_rl_prob, away_covers_rl_prob = prediction["home_covers_rl_prob"], prediction["away_covers_rl_prob"]
        fair_total_runs = prediction["fair_total_runs"]

        if price:
            away_market_prob = implied_prob(price["away"])
            home_market_prob = implied_prob(price["home"])
            away_edge = edge(away_model_prob, away_market_prob)
            home_edge = edge(home_model_prob, home_market_prob)
            away_ev = expected_value(away_model_prob, price["away"])
            home_ev = expected_value(home_model_prob, price["home"])
        else:
            away_market_prob = home_market_prob = None
            away_edge = home_edge = None
            away_ev = home_ev = None

        if no_vig:
            away_market_no_vig_prob, home_market_no_vig_prob = no_vig
        else:
            away_market_no_vig_prob = home_market_no_vig_prob = None

        # Favorito del mercado — se calcula sobre el consenso sin vig
        # cuando existe (magnitud honesta); si solo hay una cuota manual
        # (con vig incluido) se usa esa, es lo único disponible.
        fav = None
        if away_market_no_vig_prob is not None:
            fav = market_favorite(g["away_team"], g["home_team"], away_market_no_vig_prob, home_market_no_vig_prob)
        elif away_market_prob is not None:
            fav = market_favorite(g["away_team"], g["home_team"], away_market_prob, home_market_prob)

        model_edge_vs_market_favorite = None
        if fav is not None and fav["side"] is not None and away_edge is not None:
            model_edge_vs_market_favorite = home_edge if fav["side"] == "home" else away_edge

        # Candidato a revisión: edge por encima del umbral Y los dos
        # modelos independientes (heurístico + Skellam) de acuerdo en el
        # favorito. Es una preselección para que decidas tú — nunca una
        # apuesta automática.
        models_agree = (away_model_prob > 0.5) == (away_skellam_prob > 0.5)
        flag_review = bool(
            models_agree
            and away_edge is not None
            and max(abs(away_edge), abs(home_edge)) >= REVIEW_EDGE_THRESHOLD
        )

        # Picks recomendados — hasta 1 por mercado (moneyline/run_line/
        # totals), como máximo MAX_PICKS_PER_GAME por partido. Un mercado
        # sin cuotas cargadas simplemente no compite (no hay obligación de
        # que el pick de moneyline exista). Si ninguno tiene edge real,
        # FORCE_AT_LEAST_ONE_PICK genera el menos malo marcado forced=True.
        market_lines = {}
        if price:
            market_lines["moneyline"] = {
                "home_odds": price["home"], "away_odds": price["away"],
                "home_novig": home_market_no_vig_prob, "away_novig": away_market_no_vig_prob,
            }
        if manual_rl:
            market_lines["run_line"] = {
                "line": manual_rl.get("line", 1.5),
                "home_odds": manual_rl.get("home"), "away_odds": manual_rl.get("away"),
                "home_novig": rl_home_novig, "away_novig": rl_away_novig,
            }
        if manual_totals:
            market_lines["totals"] = {
                "line": manual_totals.get("line"),
                "over_odds": manual_totals.get("over"), "under_odds": manual_totals.get("under"),
                "over_novig": totals_over_novig, "under_novig": totals_under_novig,
            }

        candidates = generate_pick_candidates(prediction, market_lines,
                                               min_ev=MIN_PICK_EV, min_edge=MIN_PICK_EDGE)
        picks = select_picks_for_game(candidates, force_at_least_one=FORCE_AT_LEAST_ONE_PICK,
                                       max_picks=MAX_PICKS_PER_GAME)

        row = {
            "game_pk": g["game_pk"],
            "game_date": date.today().strftime("%Y-%m-%d"),
            "away_team": g["away_team"],
            "home_team": g["home_team"],
            "away_pitcher": g.get("away_pitcher_name"),
            "home_pitcher": g.get("home_pitcher_name"),
            "away_bullpen_era": round(away_bullpen, 2),
            "home_bullpen_era": round(home_bullpen, 2),
            "away_k_pct": away_cmd["k_pct"],
            "home_k_pct": home_cmd["k_pct"],
            "away_days_rest": away_rest["days_rest"],
            "home_days_rest": home_rest["days_rest"],
            "park_name": park["name"],
            "park_factor": park["park_factor"],
            "temp_f": weather.get("temp_f"),
            "away_proj_runs": round(away_mu, 2),
            "home_proj_runs": round(home_mu, 2),
            "away_model_prob": away_model_prob,
            "home_model_prob": home_model_prob,
            "away_skellam_prob": away_skellam_prob,
            "home_skellam_prob": home_skellam_prob,
            "home_covers_rl_prob": home_covers_rl_prob,
            "away_covers_rl_prob": away_covers_rl_prob,
            "fair_total_runs": round(fair_total_runs, 2),
            "away_market_prob": away_market_prob,
            "home_market_prob": home_market_prob,
            "away_market_no_vig_prob": away_market_no_vig_prob,
            "home_market_no_vig_prob": home_market_no_vig_prob,
            "market_favorite_team": fav["team"] if fav else None,
            "market_favorite_side": fav["side"] if fav else None,
            "market_favorite_prob": fav["prob"] if fav else None,
            "model_edge_vs_market_favorite": model_edge_vs_market_favorite,
            "away_edge": away_edge,
            "home_edge": home_edge,
            "away_ev": away_ev,
            "home_ev": home_ev,
            "flag_review": flag_review,
            "model_version": MODEL_VERSION,
            "git_commit": get_git_commit(),
            # Claves internas — run_pipeline() las extrae antes de guardar
            # en GameAnalysis (no son columnas de esa tabla): _feature_snapshot
            # es el punto-en-el-tiempo para recálculos futuros vía
            # model.predictor; _picks son los picks recomendados, que se
            # persisten en su propia tabla (Pick) vía save_picks().
            "_feature_snapshot": raw_inputs,
            "_picks": picks,
        }

        results.append(row)

    return results


def run_pipeline():
    setup_logging()
    init_db()

    # 1. Auditoría de resultados y desempeño del día(s) anterior(es)
    print("\n--- 🔍 AUDITANDO RESULTADOS DEL DÍA ANTERIOR ---")
    updated = update_results()
    print(f"Resultados actualizados: {updated}")
    print_performance_report()
    audit_live()

    # 2. Análisis
    print("\n--- ⚾ GENERANDO PREDICCIONES PARA HOY ---")
    results = analyze_today()

    if results:
        picks_by_game = {}
        all_picks_rows = []

        for r in results:
            snapshot = r.pop("_feature_snapshot", None)
            picks = r.pop("_picks", [])

            save_analysis(r)
            if snapshot is not None:
                save_feature_snapshot(r["game_pk"], r["game_date"], snapshot)
            if picks:
                save_picks(r["game_pk"], r["game_date"], picks, MODEL_VERSION)
                picks_by_game[r["game_pk"]] = picks
                for p in picks:
                    all_picks_rows.append({
                        "game_pk": r["game_pk"], "game_date": r["game_date"],
                        "away_team": r["away_team"], "home_team": r["home_team"],
                        **p,
                    })

        print_report(results, picks_by_game=picks_by_game)
        path = export_csv(results)
        print(f"\nReporte de hoy exportado a: {path}")

        if all_picks_rows:
            picks_path = export_picks_csv(all_picks_rows)
            print(f"Picks exportados a: {picks_path}")
    else:
        print("No hay juegos para analizar hoy.")


if __name__ == "__main__":
    run_pipeline()
