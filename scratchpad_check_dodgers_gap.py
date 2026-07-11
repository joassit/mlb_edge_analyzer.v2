import json
import sys

sys.path.insert(0, ".")

from db.database import SessionLocal, GameAnalysis, Pick

TARGET_PK = 823927  # Arizona Diamondbacks @ Los Angeles Dodgers, 2026-07-10

session = SessionLocal()
try:
    rows = session.query(GameAnalysis).filter(GameAnalysis.game_pk == TARGET_PK).all()
    out = {"game_pk": TARGET_PK, "n_rows_in_game_analysis": len(rows), "rows": []}
    for r in rows:
        out["rows"].append({
            "id": r.id, "game_date": r.game_date,
            "away_team": r.away_team, "home_team": r.home_team,
            "away_pitcher": r.away_pitcher, "home_pitcher": r.home_pitcher,
            "away_market_prob": r.away_market_prob, "home_market_prob": r.home_market_prob,
            "away_odds": r.away_odds, "home_odds": r.home_odds,
        })
    picks = session.query(Pick).filter(Pick.game_pk == TARGET_PK).all()
    out["n_picks"] = len(picks)

    all_0710 = session.query(GameAnalysis).filter(GameAnalysis.game_date == "2026-07-10").all()
    out["all_game_pks_0710"] = sorted({r.game_pk for r in all_0710})
finally:
    session.close()

print("===JSON_GAP_START===")
print(json.dumps(out, indent=2, default=str))
print("===JSON_GAP_END===")
