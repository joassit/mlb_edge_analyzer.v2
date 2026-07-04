"""
MLB EDGE ANALYZER — Orquestador principal (Refactorizado)
"""

from datetime import date
import logging

from logging_config import setup_logging
from data.mlb_api import get_schedule
from data.stats import (
    get_pitcher_era, get_team_ops, get_league_ops, get_bullpen_era,
    get_pitcher_command, get_pitcher_rest
)
from data.park_factors import get_park_info
from data.weather import get_game_weather

from src.mlb_edge_analyzer.models.context import (
    GameContext, TeamContext, PitcherContext, ParkContext, PredictionResult
)
from model.probability import model_prob, normalize_matchup
from model.runs_projection import project_team_runs, LEAGUE_AVG_ERA
from model.skellam_model import skellam_win_prob
from model.markets import run_line_prob, fair_total_line
from model.edge import implied_prob, edge

from db.database import init_db, save_analysis
from reports.generate_report import print_report, export_csv
from config import STARTER_WEIGHT, HOME_FIELD_ADVANTAGE, MODEL_VERSION
from version_info import get_git_commit


MARKET_ODDS: dict = {}


def build_game_context(g: dict, league_ops: dict) -> GameContext | None:
    """Construye el contexto completo de un partido"""
    try:
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

        park_info = get_park_info(g["home_team_id"])
        weather = get_game_weather(park_info["lat"], park_info["lon"], g["game_time"])

        return GameContext(
            game_pk=g["game_pk"],
            game_date=date.today(),
            away_team=TeamContext(
                team_id=g["away_team_id"],
                name=g["away_team"],
                ops=away_ops,
                bullpen_era=away_bullpen
            ),
            home_team=TeamContext(
                team_id=g["home_team_id"],
                name=g["home_team"],
                ops=home_ops,
                bullpen_era=home_bullpen
            ),
            away_pitcher=PitcherContext(
                pitcher_id=g["away_pitcher_id"],
                name=g["away_pitcher_name"],
                era=away_era,
                k_pct=away_cmd.get("k_pct", 0),
                bb_pct=away_cmd.get("bb_pct", 0),
                days_rest=away_rest.get("days_rest", 0),
                last_outing_pitches=away_rest.get("last_outing_pitches", 0)
            ),
            home_pitcher=PitcherContext(
                pitcher_id=g["home_pitcher_id"],
                name=g["home_pitcher_name"],
                era=home_era,
                k_pct=home_cmd.get("k_pct", 0),
                bb_pct=home_cmd.get("bb_pct", 0),
                days_rest=home_rest.get("days_rest", 0),
                last_outing_pitches=home_rest.get("last_outing_pitches", 0)
            ),
            park=ParkContext(
                name=park_info["name"],
                park_factor=park_info["park_factor"],
                temp_f=weather["temp_f"]
            )
        )
    except Exception as e:
        logging.warning(f"Error construyendo contexto para juego {g.get('game_pk')}: {e}")
        return None


def analyze_today() -> list[dict]:
    logger = logging.getLogger(__name__)
    league_ops = get_league_ops()
    games = get_schedule(date.today())

    results = []
    for g in games:
        if not g.get("away_pitcher_id") or not g.get("home_pitcher_id"):
            continue

        ctx = build_game_context(g, league_ops)
        if not ctx:
            continue

        # Aquí irán los cálculos de modelos usando ctx...
        # Por ahora mantenemos compatibilidad con tu reporte y DB 
        row = {
            "game_pk": ctx.game_pk,
            "away_team": ctx.away_team.name,
            "home_team": ctx.home_team.name,
            "away_pitcher": ctx.away_pitcher.name,
            "home_pitcher": ctx.home_pitcher.name,
            "away_era": ctx.away_pitcher.era,
            "home_era": ctx.home_pitcher.era,
            "away_ops": ctx.away_team.ops,
            "home_ops": ctx.home_team.ops,
            
            # Llaves corregidas para que coincidan con generate_report.py
            "away_model_prob": 0.0,
            "home_model_prob": 0.0,
            "away_skellam_prob": 0.0,
            "home_skellam_prob": 0.0,
            "away_edge": 0.0,
            "home_edge": 0.0,
            "away_implied_prob": 0.0,
            "home_implied_prob": 0.0,
            "market_odds_away": 0,
            "market_odds_home": 0,
            "projected_runs_away": 0.0,
            "projected_runs_home": 0.0,
            "park_factor": ctx.park.park_factor,
            "weather_temp": ctx.park.temp_f
        }
        results.append(row)

    logger.info(f"Se analizaron {len(results)} juegos")
    return results


if __name__ == "__main__":
    logger = setup_logging()
    logger.info("🚀 Iniciando MLB Edge Analyzer (versión con Context Objects)")

    init_db()
    rows = analyze_today()

    print_report(rows)

    for r in rows:
        save_analysis(r)

    if rows:
        path = export_csv(rows)
        print(f"\nReporte exportado a: {path}")

    logger.info(f"✅ Análisis completado: {len(rows)} juegos")