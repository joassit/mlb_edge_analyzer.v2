"""
MLB EDGE ANALYZER — Versión 0.5.0
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
from model.runs_projection import project_team_runs, LEAGUE_AVG_ERA
from model.probability import model_prob, normalize_matchup
from model.skellam_model import skellam_win_prob
from model.markets import run_line_prob, fair_total_line
from model.edge import implied_prob, edge, expected_value
from db.database import init_db, save_analysis
from reports.generate_report import print_report, export_csv
from audit_live import audit_live
from tracking.results_tracker import update_results, print_performance_report
from config import STARTER_WEIGHT, HOME_FIELD_ADVANTAGE, MODEL_VERSION
from version_info import get_git_commit

# Cuotas de mercado cargadas a mano mientras no haya una API de odds conectada
# (ver roadmap Fase 2). Llena esto con el game_pk de cada partido (lo ves
# corriendo `python data/mlb_api.py`) y las cuotas moneyline reales.
MARKET_ODDS = {
    # 717468: {"away": -135, "home": +115},
}


def analyze_today() -> list[dict]:
    league_ops = get_league_ops()
    games = get_schedule(date.today())
    weather_by_team = preload_weather(games, get_park_info)
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

        # Carreras proyectadas (Skellam) — insumo de ambos modelos de probabilidad
        away_mu = project_team_runs(away_ops, home_era, away_bullpen, league_ops, LEAGUE_AVG_ERA, park["park_factor"], STARTER_WEIGHT, is_home=False, temp_f=weather.get("temp_f"))
        home_mu = project_team_runs(home_ops, away_era, home_bullpen, league_ops, LEAGUE_AVG_ERA, park["park_factor"], STARTER_WEIGHT, is_home=True, temp_f=weather.get("temp_f"))

        # Modelo heurístico (ERA/OPS + ajustes) normalizado para que sume 1
        away_p_raw = model_prob(
            away_era, away_ops, league_ops, bullpen_era=away_bullpen, starter_weight=STARTER_WEIGHT,
            k_pct=away_cmd["k_pct"], bb_pct=away_cmd["bb_pct"],
            days_rest=away_rest["days_rest"], last_outing_pitches=away_rest["last_outing_pitches"],
            park_factor=park["park_factor"], temp_f=weather.get("temp_f"),
        )
        home_p_raw = model_prob(
            home_era, home_ops, league_ops, bullpen_era=home_bullpen, starter_weight=STARTER_WEIGHT,
            k_pct=home_cmd["k_pct"], bb_pct=home_cmd["bb_pct"],
            days_rest=home_rest["days_rest"], last_outing_pitches=home_rest["last_outing_pitches"],
            park_factor=park["park_factor"], temp_f=weather.get("temp_f"),
        )
        away_model_prob, home_model_prob = normalize_matchup(away_p_raw, home_p_raw, HOME_FIELD_ADVANTAGE)

        # Segundo modelo independiente: Skellam sobre las carreras proyectadas
        home_skellam_prob = skellam_win_prob(home_mu, away_mu)
        away_skellam_prob = 1.0 - home_skellam_prob

        # Mercados adicionales, misma proyección de carreras
        home_covers_rl_prob, away_covers_rl_prob = run_line_prob(home_mu, away_mu)
        fair_total_runs = fair_total_line(home_mu, away_mu)

        # Edge/EV contra el mercado, solo si hay cuotas cargadas para este juego
        odds = MARKET_ODDS.get(g["game_pk"])
        if odds:
            away_market_prob = implied_prob(odds["away"])
            home_market_prob = implied_prob(odds["home"])
            away_edge = edge(away_model_prob, away_market_prob)
            home_edge = edge(home_model_prob, home_market_prob)
            away_ev = expected_value(away_model_prob, odds["away"])
            home_ev = expected_value(home_model_prob, odds["home"])
        else:
            away_market_prob = home_market_prob = None
            away_edge = home_edge = None
            away_ev = home_ev = None

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
            "away_edge": away_edge,
            "home_edge": home_edge,
            "away_ev": away_ev,
            "home_ev": home_ev,
            "model_version": MODEL_VERSION,
            "git_commit": get_git_commit(),
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
        print_report(results)
        for r in results:
            save_analysis(r)
        path = export_csv(results)
        print(f"\nReporte de hoy exportado a: {path}")
    else:
        print("No hay juegos para analizar hoy.")


if __name__ == "__main__":
    run_pipeline()
