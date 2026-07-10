import json
import subprocess
import sys

sys.path.insert(0, ".")

from db.database import SessionLocal, GameAnalysis, Pick

TODAY = "2026-07-10"

out = {}
session = SessionLocal()
try:
    rows = session.query(GameAnalysis).filter(GameAnalysis.game_date == TODAY).order_by(GameAnalysis.id.desc()).all()
    seen = set()
    deduped = []
    for r in rows:
        if r.game_pk in seen:
            continue
        seen.add(r.game_pk)
        deduped.append(r)

    picks = session.query(Pick).filter(Pick.game_date == TODAY).all()
    picks_by_pk = {}
    for p in picks:
        picks_by_pk.setdefault(p.game_pk, []).append(p)

    fichas = []
    for r in sorted(deduped, key=lambda r: r.game_pk):
        pks = picks_by_pk.get(r.game_pk, [])
        ml_pick = next((p for p in pks if p.market == "moneyline"), None)
        fichas.append({
            "game_pk": r.game_pk,
            "away_team": r.away_team, "home_team": r.home_team,
            "away_pitcher": r.away_pitcher, "home_pitcher": r.home_pitcher,
            "park_name": r.park_name, "temp_f": r.temp_f,
            "away_proj_runs": r.away_proj_runs, "home_proj_runs": r.home_proj_runs,
            "away_skellam_prob": r.away_skellam_prob, "home_skellam_prob": r.home_skellam_prob,
            "away_market_prob": r.away_market_prob, "home_market_prob": r.home_market_prob,
            "away_odds": r.away_odds, "home_odds": r.home_odds,
            "market_favorite_side": r.market_favorite_side,
            "flag_review": r.flag_review,
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
    out["today_n_games"] = len(deduped)
    out["today_n_forced"] = sum(1 for f in fichas if f["pick"] and f["pick"]["forced"])
    out["today_n_no_pick"] = sum(1 for f in fichas if f["pick"] is None)
finally:
    session.close()

try:
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    out["git_commit"] = commit
except Exception as e:
    out["git_meta_error"] = str(e)

print("===JSON_TODAY_PICKS_START===")
print(json.dumps(out, indent=2, default=str))
print("===JSON_TODAY_PICKS_END===")
