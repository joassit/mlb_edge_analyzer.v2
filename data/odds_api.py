"""
Integración con The Odds API (https://the-odds-api.com) para cuotas
moneyline en tiempo real.

Requiere la variable de entorno ODDS_API_KEY — nunca se hardcodea ni se
commitea. Si falta la key, o la llamada falla, o la respuesta tiene un
esquema inesperado: se degrada a lista vacía. `analyze_today()` cae a
`MARKET_ODDS` manual (o a "sin cuotas") en ese caso — un problema de la API
de odds nunca debe tumbar el análisis del día.
"""

import logging
import os

import requests

from data.contracts import require, SchemaError

logger = logging.getLogger("mlb_edge_analyzer")

ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"


def _normalize_team_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def fetch_moneyline_odds() -> list[dict]:
    """
    Trae las cuotas moneyline (h2h) actuales de todos los bookmakers
    disponibles en la región US. Devuelve [] si falta la API key, si la
    llamada falla, o si el esquema de la respuesta no es el esperado.
    """
    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        logger.info("ODDS_API_KEY no configurada — se omite la consulta de cuotas en vivo.")
        return []

    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": "h2h",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    try:
        resp = requests.get(ODDS_API_BASE, params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as e:
        logger.warning(f"No se pudo obtener cuotas de The Odds API: {e}")
        return []

    if not isinstance(payload, list):
        logger.warning(f"Respuesta inesperada de The Odds API (se esperaba una lista): {type(payload)}")
        return []

    events = []
    for raw_event in payload:
        home_team = require(raw_event, ["home_team"], "odds_api.event")
        away_team = require(raw_event, ["away_team"], "odds_api.event")
        commence_time = require(raw_event, ["commence_time"], "odds_api.event")
        if isinstance(home_team, SchemaError):
            logger.warning(str(home_team))
            continue
        if isinstance(away_team, SchemaError):
            logger.warning(str(away_team))
            continue
        if isinstance(commence_time, SchemaError):
            commence_time = None

        prices = []
        for book in raw_event.get("bookmakers", []):
            h2h = next((m for m in book.get("markets", []) if m.get("key") == "h2h"), None)
            if not h2h:
                continue
            outcomes = {o.get("name"): o.get("price") for o in h2h.get("outcomes", [])}
            away_price = outcomes.get(away_team)
            home_price = outcomes.get(home_team)
            if away_price is None or home_price is None:
                continue
            prices.append({
                "book": book.get("key"),
                "away_price": away_price,
                "home_price": home_price,
                "last_update": h2h.get("last_update") or book.get("last_update"),
            })

        events.append({
            "home_team": home_team,
            "away_team": away_team,
            "commence_time": commence_time,
            "prices": prices,
        })

    return events


def match_odds_to_game(events: list[dict], away_team: str, home_team: str) -> dict | None:
    """Empareja un juego de la MLB Stats API contra los eventos de The Odds
    API por nombre de equipo (normalizado). None si no hay match."""
    away_norm = _normalize_team_name(away_team)
    home_norm = _normalize_team_name(home_team)
    for event in events:
        if (_normalize_team_name(event["away_team"]) == away_norm
                and _normalize_team_name(event["home_team"]) == home_norm):
            return event
    return None


def consensus_no_vig_prob(event: dict) -> tuple[float, float] | None:
    """
    Promedia la probabilidad sin vig de cada bookmaker disponible para este
    evento — el consenso "justo" del mercado, usado como referencia para
    medir edge real y (más adelante) Closing Line Value. None si ningún
    bookmaker trajo datos usables para este evento.
    """
    from model.edge import no_vig_probs

    if not event["prices"]:
        return None

    away_probs, home_probs = [], []
    for p in event["prices"]:
        away_p, home_p = no_vig_probs(p["away_price"], p["home_price"])
        away_probs.append(away_p)
        home_probs.append(home_p)

    return sum(away_probs) / len(away_probs), sum(home_probs) / len(home_probs)


def best_available_price(event: dict) -> dict | None:
    """
    La mejor cuota tomable por lado (la que de verdad podrías apostar) —
    distinta del consenso no-vig, que sirve para medir edge/CLV, no para
    ejecutar la apuesta. None si no hay bookmakers con datos usables.
    """
    if not event["prices"]:
        return None

    best_away = max(p["away_price"] for p in event["prices"])
    best_home = max(p["home_price"] for p in event["prices"])
    return {"away": best_away, "home": best_home}
