"""
Cuotas reales de mercado vía The Odds API (https://the-odds-api.com).

Tier gratuito: 500 requests/mes — suficiente para 1-2 consultas diarias
(cada consulta trae TODOS los juegos MLB del día en una sola llamada).

Configuración (opcional — sin key, el sistema funciona igual que antes):
  1. Regístrate gratis en https://the-odds-api.com y copia tu API key
  2. En PowerShell, antes de correr main.py:
       $env:ODDS_API_KEY = "tu_key_aqui"
     O para dejarla permanente en Windows:
       [Environment]::SetEnvironmentVariable("ODDS_API_KEY", "tu_key_aqui", "User")
     (cierra y reabre la terminal después de la permanente)
"""

import logging
import os
import requests

logger = logging.getLogger("mlb_edge_analyzer")

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")

# Mapeo de nombres: The Odds API usa nombres completos que coinciden con
# los de la MLB Stats API en casi todos los casos, así que emparejamos
# por nombre de equipo local + visitante.


def fetch_moneyline_odds() -> dict[tuple[str, str], dict]:
    """
    Trae las cuotas moneyline de todos los juegos MLB de hoy.

    Devuelve un dict indexado por (away_team, home_team):
      {("St. Louis Cardinals", "Chicago Cubs"): {"away": -135, "home": +115}, ...}

    Si no hay API key configurada o la consulta falla, devuelve {} y el
    sistema sigue funcionando sin cuotas (igual que siempre).
    """
    if not ODDS_API_KEY:
        logger.info("ODDS_API_KEY no configurada — corriendo sin cuotas de mercado")
        return {}

    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h",  # h2h = moneyline
        "oddsFormat": "american",
    }

    try:
        resp = requests.get(f"{ODDS_API_BASE}/sports/baseball_mlb/odds",
                            params=params, timeout=15)
        resp.raise_for_status()
        games = resp.json()

        remaining = resp.headers.get("x-requests-remaining")
        if remaining is not None:
            logger.info(f"The Odds API: {remaining} requests restantes este mes")

    except requests.RequestException as e:
        logger.warning(f"No se pudieron obtener cuotas de The Odds API: {e}")
        return {}

    odds_by_matchup: dict[tuple[str, str], dict] = {}

    for game in games:
        away = game.get("away_team")
        home = game.get("home_team")
        bookmakers = game.get("bookmakers", [])
        if not away or not home or not bookmakers:
            continue

        # Usamos el primer bookmaker disponible. (Mejora futura: promediar
        # varios, o dejar elegir la casa preferida.)
        h2h = next(
            (m for m in bookmakers[0].get("markets", []) if m.get("key") == "h2h"),
            None,
        )
        if not h2h:
            continue

        prices = {o["name"]: o["price"] for o in h2h.get("outcomes", [])}
        if away in prices and home in prices:
            odds_by_matchup[(away, home)] = {
                "away": prices[away],
                "home": prices[home],
                "bookmaker": bookmakers[0].get("title", "?"),
            }

    logger.info(f"Cuotas obtenidas para {len(odds_by_matchup)} juegos")
    return odds_by_matchup
