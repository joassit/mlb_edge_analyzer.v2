"""
MLB EDGE ANALYZER — Orquestador principal (Etapa 1)

Flujo:
  1. Obtener juegos del día + pitchers probables (MLB Stats API)
  2. Obtener ERA de cada abridor y OPS de cada ofensiva
  3. Calcular probabilidad del modelo
  4. (Opcional) Comparar contra cuotas de mercado si las cargaste
  5. Guardar en base de datos
  6. Imprimir y exportar reporte

Uso:
    python main.py
"""

import argparse
from datetime import date

from logging_config import setup_logging
from data.mlb_api import get_schedule
from data.stats import (
    get_pitcher_era, get_team_ops, get_league_ops, get_bullpen_era,
    get_pitcher_command, get_pitcher_rest, get_team_batting_advanced,
)
from data.park_factors import get_park_info
from data.weather import get_game_weather, preload_weather
from data.odds_api import fetch_moneyline_odds
from model.probability import model_prob, normalize_matchup
from model.runs_projection import project_team_runs, LEAGUE_AVG_ERA
from model.skellam_model import skellam_win_prob
from model.markets import run_line_prob, totals_prob, fair_total_line
from model.edge import implied_prob, edge, fair_odds, expected_value, no_vig_probs
from db.database import init_db, save_analysis
from reports.generate_report import print_report, export_csv
from config import STARTER_WEIGHT, HOME_FIELD_ADVANTAGE, MODEL_VERSION
from version_info import get_git_commit

# -----------------------------------------------------------------
# Cuotas de mercado (opcional). Mientras no tengas una API de odds
# conectada, puedes llenar esto a mano por game_pk:
#   MARKET_ODDS = {717468: {"away": -135, "home": +115}}
# -----------------------------------------------------------------
MARKET_ODDS: dict[int, dict] = {}


def build_prediction_row(g: dict, league_ops: float, weather_by_team: dict,
                          auto_odds: dict | None = None) -> dict | None:
    """
    Calcula la predicción completa de UN juego. Devuelve None si falta
    algún dato esencial (ERA/OPS). Reutilizable tanto por analyze_today()
    (juegos de hoy sin empezar) como por daily_report.py (auditoría).
    """
    if not g["away_pitcher_id"] or not g["home_pitcher_id"]:
        return None

    away_era = get_pitcher_era(g["away_pitcher_id"])
    home_era = get_pitcher_era(g["home_pitcher_id"])
    away_ops = get_team_ops(g["away_team_id"])
    home_ops = get_team_ops(g["home_team_id"])

    if None in (away_era, home_era, away_ops, home_ops):
        return None

    away_bullpen = get_bullpen_era(g["away_team_id"])
    home_bullpen = get_bullpen_era(g["home_team_id"])

    away_cmd = get_pitcher_command(g["away_pitcher_id"])
    home_cmd = get_pitcher_command(g["home_pitcher_id"])
    away_rest = get_pitcher_rest(g["away_pitcher_id"])
    home_rest = get_pitcher_rest(g["home_pitcher_id"])

    park = get_park_info(g["home_team_id"])
    weather = weather_by_team.get(g["home_team_id"], {"temp_f": None, "wind_mph": None, "wind_direction_deg": None})

    away_raw = model_prob(
        away_era, away_ops, league_ops,
        bullpen_era=away_bullpen, starter_weight=STARTER_WEIGHT,
        k_pct=away_cmd["k_pct"], bb_pct=away_cmd["bb_pct"],
        days_rest=away_rest["days_rest"], last_outing_pitches=away_rest["last_outing_pitches"],
        park_factor=park["park_factor"], temp_f=weather["temp_f"],
    )
    home_raw = model_prob(
        home_era, home_ops, league_ops,
        bullpen_era=home_bullpen, starter_weight=STARTER_WEIGHT,
        k_pct=home_cmd["k_pct"], bb_pct=home_cmd["bb_pct"],
        days_rest=home_rest["days_rest"], last_outing_pitches=home_rest["last_outing_pitches"],
        park_factor=park["park_factor"], temp_f=weather["temp_f"],
    )
    away_model, home_model = normalize_matchup(away_raw, home_raw,
                                                 home_field_adv=HOME_FIELD_ADVANTAGE)

    away_mu = project_team_runs(
        away_ops, home_era, home_bullpen, league_ops,
        LEAGUE_AVG_ERA, park["park_factor"], STARTER_WEIGHT, is_home=False,
    )
    home_mu = project_team_runs(
        home_ops, away_era, away_bullpen, league_ops,
        LEAGUE_AVG_ERA, park["park_factor"], STARTER_WEIGHT, is_home=True,
    )
    away_skellam = skellam_win_prob(away_mu, home_mu)
    home_skellam = skellam_win_prob(home_mu, away_mu)

    home_covers_rl, away_covers_rl = run_line_prob(home_mu, away_mu, line=1.5)
    fair_total = fair_total_line(home_mu, away_mu)

    row = {
        "game_pk": g["game_pk"],
        "game_date": date.today().strftime("%Y-%m-%d"),
        "away_team": g["away_team"],
        "home_team": g["home_team"],
        "away_pitcher": g["away_pitcher_name"],
        "home_pitcher": g["home_pitcher_name"],
        "away_pitcher_id": g["away_pitcher_id"],
        "home_pitcher_id": g["home_pitcher_id"],
        "away_bullpen_era": round(away_bullpen, 2),
        "home_bullpen_era": round(home_bullpen, 2),
        "away_k_pct": round(away_cmd["k_pct"], 3) if away_cmd["k_pct"] else None,
        "home_k_pct": round(home_cmd["k_pct"], 3) if home_cmd["k_pct"] else None,
        "away_days_rest": away_rest["days_rest"],
        "home_days_rest": home_rest["days_rest"],
        "park_factor": park["park_factor"],
        "park_name": park["name"],
        "temp_f": weather["temp_f"],
        "away_model_prob": away_model,
        "home_model_prob": home_model,
        "away_proj_runs": round(away_mu, 2),
        "home_proj_runs": round(home_mu, 2),
        "away_skellam_prob": round(away_skellam, 3),
        "home_skellam_prob": round(home_skellam, 3),
        "home_covers_rl_prob": round(home_covers_rl, 3),
        "away_covers_rl_prob": round(away_covers_rl, 3),
        "fair_total_runs": round(fair_total, 2),
        "away_market_prob": None,
        "home_market_prob": None,
        "away_edge": None,
        "home_edge": None,
        "decision": None,
        "model_version": MODEL_VERSION,
        "git_commit": get_git_commit(),
    }

    # Prioridad de cuotas: manuales (MARKET_ODDS) > automáticas (The Odds API)
    odds = MARKET_ODDS.get(g["game_pk"])
    if odds is None and auto_odds:
        odds = auto_odds.get((g["away_team"], g["home_team"]))

    if odds:
        away_imp = implied_prob(odds["away"])
        home_imp = implied_prob(odds["home"])
        away_novig, home_novig = no_vig_probs(odds["away"], odds["home"])
        row["away_market_prob"] = away_imp
        row["home_market_prob"] = home_imp
        row["away_edge"] = edge(away_model, away_imp)
        row["home_edge"] = edge(home_model, home_imp)
        row["away_edge_novig"] = edge(away_model, away_novig)
        row["home_edge_novig"] = edge(home_model, home_novig)
        row["away_ev"] = expected_value(away_model, odds["away"])
        row["home_ev"] = expected_value(home_model, odds["home"])
        row["odds_source"] = odds.get("bookmaker", "manual")

    return row


def analyze_today() -> list[dict]:
    league_ops = get_league_ops()
    games = get_schedule(date.today())

    # Clima de todos los estadios de hoy, en paralelo, una sola vez
    # (antes: una llamada secuencial por juego, con timeouts acumulándose)
    weather_by_team = preload_weather(games, get_park_info)

    # Cuotas reales de mercado (una sola llamada para todos los juegos).
    # Si no hay ODDS_API_KEY configurada, devuelve {} y todo sigue igual.
    auto_odds = fetch_moneyline_odds()

    results = []

    for g in games:
        if g.get("abstract_state") != "Preview":
            # El juego ya empezó o ya terminó — no tiene sentido "predecirlo"
            continue

        row = build_prediction_row(g, league_ops, weather_by_team, auto_odds)
        if row:
            results.append(row)

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MLB Edge Analyzer")
    parser.add_argument("--backtest", action="store_true",
                         help="Corre la evaluación histórica (backtest.py) en vez del análisis del día")
    args = parser.parse_args()

    logger = setup_logging()

    if args.backtest:
        from backtest import evaluate_model, print_backtest_report, export_backtest_csv

        logger.info("Corriendo backtest histórico")
        metrics = evaluate_model()
        print_backtest_report(metrics)
        if metrics["n_games"] > 0:
            path = export_backtest_csv(metrics)
            print(f"Backtest exportado a: {path}")
        logger.info(f"Backtest terminado: {metrics['n_games']} juego(s) evaluados")
    else:
        logger.info("Iniciando análisis del día")

        init_db()
        rows = analyze_today()
        print_report(rows)

        for r in rows:
            save_analysis(r)

        if rows:
            path = export_csv(rows)
            print(f"\nReporte exportado a: {path}")

        logger.info(f"Análisis terminado: {len(rows)} juegos procesados")
