"""
Script temporal (corre DENTRO de un workflow de GitHub Actions, contra la
mlb_edge.db real descargada del artifact de la corrida de hoy) que arma un
JSON con TODO lo necesario para el Technical Performance Report de
2026-07-12 -- usando exclusivamente las funciones OFICIALES de
tracking/results_tracker.py (nunca recalculando a mano).
"""
import json
import subprocess
import sys

sys.path.insert(0, ".")

from db.database import SessionLocal, GameAnalysis, ActualResult, Pick
from tracking.results_tracker import (
    compute_metrics, compute_calibration, compute_pick_performance,
    compute_bet_performance, compute_daily_review,
    count_liquidated_picks_with_market_odds, validate_probabilities,
)

TODAY = "2026-07-12"
YESTERDAY = "2026-07-11"

out = {}

out["compute_metrics_30"] = compute_metrics(days=30)
out["compute_calibration_90"] = compute_calibration(days=90)
out["compute_pick_performance_30"] = compute_pick_performance(days=30)
out["compute_bet_performance_30"] = compute_bet_performance(days=30)
out["liquidated_with_market_odds"] = count_liquidated_picks_with_market_odds()

try:
    out["daily_review_yesterday"] = compute_daily_review(YESTERDAY)
except Exception as e:
    out["daily_review_yesterday_error"] = str(e)

try:
    out["daily_review_today"] = compute_daily_review(TODAY)
except Exception as e:
    out["daily_review_today_error"] = str(e)

session = SessionLocal()
try:
    all_analysis = session.query(GameAnalysis).order_by(GameAnalysis.game_date, GameAnalysis.id).all()
    by_date = {}
    for r in all_analysis:
        by_date.setdefault(r.game_date, []).append(r)

    dist = []
    for d, rows in sorted(by_date.items()):
        seen = set()
        deduped = []
        for r in sorted(rows, key=lambda r: -r.id):
            if r.game_pk in seen:
                continue
            seen.add(r.game_pk)
            deduped.append(r)
        n_games = len(deduped)
        n_with_result = sum(
            1 for r in deduped
            if session.query(ActualResult).filter(ActualResult.game_pk == r.game_pk).first() is not None
        )
        n_with_market = sum(1 for r in deduped if r.away_market_prob is not None)
        picks_that_date = session.query(Pick).filter(Pick.game_date == d).all()
        pick_pks = {p.game_pk for p in picks_that_date}
        n_forced = len({p.game_pk for p in picks_that_date if p.forced})
        dist.append({
            "date": d, "n_games": n_games, "n_with_result": n_with_result,
            "n_with_market": n_with_market, "n_picks_games": len(pick_pks),
            "n_forced_games": n_forced,
        })
    out["distribution_by_date"] = dist
    out["total_games_with_prediction"] = sum(d["n_games"] for d in dist)

    today_rows = [r for r in all_analysis if r.game_date == TODAY]
    seen = set()
    today_deduped = []
    for r in sorted(today_rows, key=lambda r: -r.id):
        if r.game_pk in seen:
            continue
        seen.add(r.game_pk)
        today_deduped.append(r)

    today_picks = session.query(Pick).filter(Pick.game_date == TODAY).all()
    picks_by_pk = {}
    for p in today_picks:
        picks_by_pk.setdefault(p.game_pk, []).append(p)

    fichas = []
    for r in sorted(today_deduped, key=lambda r: r.game_pk):
        pks = picks_by_pk.get(r.game_pk, [])
        ml_pick = next((p for p in pks if p.market == "moneyline"), None)
        fichas.append({
            "game_pk": r.game_pk,
            "away_team": r.away_team, "home_team": r.home_team,
            "away_pitcher": r.away_pitcher, "home_pitcher": r.home_pitcher,
            "park_name": r.park_name, "temp_f": r.temp_f,
            "away_proj_runs": r.away_proj_runs, "home_proj_runs": r.home_proj_runs,
            "away_skellam_prob": r.away_skellam_prob, "home_skellam_prob": r.home_skellam_prob,
            "away_model_prob": r.away_model_prob, "home_model_prob": r.home_model_prob,
            "away_market_prob": r.away_market_prob, "home_market_prob": r.home_market_prob,
            "away_odds": r.away_odds, "home_odds": r.home_odds,
            "market_favorite_side": r.market_favorite_side,
            "model_edge_vs_market_favorite": r.model_edge_vs_market_favorite,
            "away_edge": r.away_edge, "home_edge": r.home_edge,
            "away_ev": r.away_ev, "home_ev": r.home_ev,
            "flag_review": r.flag_review,
            "high_confidence": getattr(r, "high_confidence", None),
            "pick": None if ml_pick is None else {
                "selection": ml_pick.selection, "model_prob": ml_pick.model_prob,
                "market_prob": ml_pick.market_prob, "edge": ml_pick.edge, "ev": ml_pick.ev,
                "odds_used": ml_pick.odds_used, "forced": ml_pick.forced,
                "prob_source": ml_pick.prob_source,
                "directional_discrepancy": ml_pick.directional_discrepancy,
                "calibration_phase": ml_pick.calibration_phase,
            },
        })
    out["today_fichas"] = fichas
    out["today_n_games"] = len(today_deduped)
    out["today_n_forced"] = sum(1 for f in fichas if f["pick"] and f["pick"]["forced"])
    out["today_n_no_pick"] = sum(1 for f in fichas if f["pick"] is None)

    invalid = [r.game_pk for r in all_analysis if not validate_probabilities(r)]
    out["invalid_probability_rows_game_pks"] = invalid

finally:
    session.close()

try:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    commit_count = subprocess.run(["git", "rev-list", "--count", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    out["git_commit"] = commit
    out["git_commit_count"] = commit_count
except Exception as e:
    out["git_meta_error"] = str(e)

print("===JSON_REPORT_START===")
print(json.dumps(out, indent=2, default=str))
print("===JSON_REPORT_END===")
