"""
Captura la cuota de cierre para las apuestas moneyline pendientes de hoy y
calcula su CLV (ver db.database.record_closing_odds).

Pensado para correr UNA vez, cerca del inicio de los juegos del día — no en
un loop ni con frecuencia alta: cada corrida consume una llamada real a The
Odds API si el caché ya venció (ver ODDS_API_CACHE_TTL_SECONDS/
ODDS_API_MONTHLY_BUDGET en config.py, la API más limitada del proyecto).

Deliberadamente NO tiene un workflow de GitHub Actions asociado: un runner
de GitHub Actions parte de un checkout limpio del repo en cada corrida y no
comparte el archivo mlb_edge.db de tu máquina (SQLite no vive en git, está
en .gitignore) — un cron ahí encontraría 0 apuestas pendientes siempre y
sería una automatización que aparenta funcionar sin hacer nada. Si en algún
momento el pipeline corre contra una base de datos compartida (Postgres),
ahí sí conviene moverlo a un Action programado.

Uso:
    python scripts/capture_closing_lines.py
    # o agenda esto en tu propio cron/Task Scheduler, apuntando al mismo
    # DATABASE_URL que usa main.py, cerca de la hora de inicio de los juegos.
"""

from datetime import date

from data.mlb_api import get_schedule
from data.odds_api import fetch_moneyline_odds, match_odds_to_game, best_available_price
from db.database import init_db, get_pending_moneyline_bets, record_closing_odds


def capture_closing_lines() -> int:
    init_db()
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")

    pending = get_pending_moneyline_bets(today_str)
    if not pending:
        print("No hay apuestas moneyline pendientes de cuota de cierre hoy.")
        return 0

    games_by_pk = {g["game_pk"]: g for g in get_schedule(today)}
    odds_events = fetch_moneyline_odds()

    updated = 0
    for bet in pending:
        game = games_by_pk.get(bet["game_pk"])
        if not game:
            continue
        event = match_odds_to_game(odds_events, game["away_team"], game["home_team"])
        if not event:
            continue
        price = best_available_price(event)
        if not price:
            continue
        record_closing_odds(bet["game_pk"], bet["side"], price[bet["side"]])
        updated += 1

    print(f"Cuotas de cierre registradas para {updated} apuesta(s) de {len(pending)} pendiente(s).")
    return updated


if __name__ == "__main__":
    capture_closing_lines()
