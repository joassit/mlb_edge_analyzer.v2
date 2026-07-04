"""
MLB EDGE ANALYZER — Versión 0.4.0
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
from db.database import init_db, save_analysis
from reports.generate_report import print_report, export_csv
from audit_live import audit_live 
from sync_results import sync_results 
from config import STARTER_WEIGHT, MODEL_VERSION
from version_info import get_git_commit

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
        park = get_park_info(g["home_team_id"])
        weather = weather_by_team.get(g["home_team_id"], {"temp_f": None})

        # Cálculos de modelo
        away_mu = project_team_runs(away_ops, home_era, away_bullpen, league_ops, LEAGUE_AVG_ERA, park["park_factor"], STARTER_WEIGHT, is_home=False, temp_f=weather.get("temp_f"))
        home_mu = project_team_runs(home_ops, away_era, home_bullpen, league_ops, LEAGUE_AVG_ERA, park["park_factor"], STARTER_WEIGHT, is_home=True, temp_f=weather.get("temp_f"))
        
        # Construcción completa del diccionario con todas las llaves esperadas por reports/
        row = {
            "game_pk": g["game_pk"],
            "game_date": date.today().strftime("%Y-%m-%d"),
            "away_team": g["away_team"],
            "home_team": g["home_team"],
            "away_pitcher": g.get("away_pitcher_name"),
            "home_pitcher": g.get("home_pitcher_name"),
            "away_bullpen_era": round(away_bullpen, 2),
            "home_bullpen_era": round(home_bullpen, 2),
            "park_name": park["name"],
            "park_factor": park["park_factor"],
            "temp_f": weather.get("temp_f"),
            "away_proj_runs": round(away_mu, 2),
            "home_proj_runs": round(home_mu, 2),
            "model_version": MODEL_VERSION,
            "git_commit": get_git_commit(),
            # Valores por defecto para prevenir KeyError en reportes
            "away_k_pct": 0.0, "home_k_pct": 0.0,
            "away_days_rest": 0, "home_days_rest": 0,
            "away_model_prob": 0.5, "home_model_prob": 0.5,
            "away_skellam_prob": 0.5, "home_skellam_prob": 0.5,
            "home_covers_rl_prob": 0.5, "away_covers_rl_prob": 0.5,
            "fair_total_runs": round(away_mu + home_mu, 2)
        }
        
        results.append(row)

    return results

def run_pipeline():
    setup_logging()
    init_db()

    # 1. Auditoría
    print("\n--- 🔍 AUDITANDO RESULTADOS DEL DÍA ANTERIOR ---")
    sync_results()
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