"""
Integración con The Odds API (https://the-odds-api.com) para cuotas
moneyline en tiempo real.

Requiere la variable de entorno ODDS_API_KEY (una sola key) o ODDS_API_KEYS
(varias, separadas por coma, ver config.resolve_odds_api_keys) — nunca se
hardcodean ni se commitean. Si no hay ninguna key configurada, o todas las
llamadas fallan, o la respuesta tiene un esquema inesperado: se degrada a
lista vacía. `analyze_today()` cae a `MARKET_ODDS` manual (o a "sin
cuotas") en ese caso — un problema de la API de odds nunca debe tumbar el
análisis del día.

Esta es la API más limitada de todas las que usa el proyecto (~500
requests/mes en el free tier, contra MLB Stats API y Open-Meteo que no
tienen ese techo). Por eso, a diferencia de las otras integraciones, esta
lleva protecciones adicionales:
  1. Caché con TTL corto (ODDS_API_CACHE_TTL_SECONDS) — un refresh del
     dashboard o una corrida repetida del pipeline el mismo día no cuenta
     como una llamada nueva mientras el caché siga vigente.
  2. Un presupuesto mensual POR KEY (ODDS_API_MONTHLY_BUDGET) — si la key
     en uso ya agotó su presupuesto del mes, o responde 401/429, o falla
     por red, se rota a la siguiente key configurada antes de degradarse
     al último caché conocido (aunque esté vencido).
"""

import hashlib
import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta

import requests

from config import ODDS_API_CACHE_TTL_SECONDS, ODDS_API_MONTHLY_BUDGET, ODDS_CACHE_DIR, resolve_odds_api_keys
from data.contracts import require, SchemaError
from data.quote_gate import gate_quote

logger = logging.getLogger("mlb_edge_analyzer")

ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"

# Enmascara el VALOR de cualquier parámetro de query cuyo nombre contenga
# "key" o "token" (case-insensitive) -- requests.RequestException incluye
# la URL completa (con apiKey=<valor real> en el query string) en su propio
# str(), y esos logs se suben como artifact de GitHub Actions (retención 30
# días, repo público). Conserva host/status/todo lo demás intacto.
_SENSITIVE_PARAM_RE = re.compile(r"(?i)([?&][^?&=\s]*(?:key|token)[^?&=\s]*=)[^&\s]+")


def _sanitize(exc_or_text) -> str:
    """Versión segura-de-loggear de una excepción o texto que pueda traer
    una URL con credenciales -- aplicar en TODO punto de log de este módulo
    que pueda incluir la excepción cruda de una llamada HTTP."""
    return _SENSITIVE_PARAM_RE.sub(r"\1***", str(exc_or_text))


def _normalize_team_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _cache_file() -> str:
    os.makedirs(ODDS_CACHE_DIR, exist_ok=True)
    return os.path.join(ODDS_CACHE_DIR, "moneyline_odds_cache.json")


def _budget_file() -> str:
    os.makedirs(ODDS_CACHE_DIR, exist_ok=True)
    return os.path.join(ODDS_CACHE_DIR, "request_budget.json")


def _read_cache(ignore_ttl: bool = False) -> list | None:
    """Lee el último payload crudo cacheado. Con ignore_ttl=True devuelve
    el caché aunque esté vencido — usado como último recurso si ya no
    queda presupuesto mensual para pedir uno fresco."""
    path = _cache_file()
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            cached = json.load(f)
        age = time.time() - cached["fetched_at"]
        if not ignore_ttl and age > ODDS_API_CACHE_TTL_SECONDS:
            return None
        return cached["payload"]
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def _cache_fetched_at() -> float | None:
    """Timestamp UNIX del caché actual, sin aplicar TTL -- separado de
    _read_cache() (que sí aplica TTL y solo devuelve el payload) para no
    tocar su contrato ya cubierto por tests existentes."""
    path = _cache_file()
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)["fetched_at"]
    except (json.JSONDecodeError, KeyError, OSError):
        return None


_last_fetch_meta: dict = {"source": "none", "fetched_at": None}


def get_last_fetch_meta() -> dict:
    """Metadata de la última llamada a fetch_moneyline_odds(): de dónde
    salieron las cuotas ('api_live' | 'api_cache' | 'api_stale_cache' |
    'none') y el timestamp UNIX de cuándo se capturaron (None si no
    aplica). Para reportar fuente/antigüedad de la cuota sin adivinar --
    ver reports/generate_report.py."""
    return dict(_last_fetch_meta)


def _atomic_write_json(path: str, data) -> None:
    """
    Escribe JSON de forma atómica: a un archivo temporal en el mismo
    directorio, luego os.replace() (atómico en POSIX) sobre el destino
    final -- open(path, "w") directo trunca el archivo ANTES de escribir
    nada, así que un crash/excepción a mitad de la escritura deja el
    archivo real vacío/corrupto. Con esto, una escritura fallida nunca
    toca el archivo existente -- el temporal corrupto se descarta solo.

    NO es multi-proceso-seguro: dos procesos escribiendo al mismo tiempo
    pueden pisarse (el último os.replace() gana) -- un lock inter-proceso
    (ej. fcntl) queda fuera de alcance, esto solo evita corrupción por
    escritura parcial, no condiciones de carrera entre procesos.
    """
    tmp_path = f"{path}.tmp{os.getpid()}"
    with open(tmp_path, "w") as f:
        json.dump(data, f)
    os.replace(tmp_path, path)


def _write_cache(payload: list) -> None:
    try:
        _atomic_write_json(_cache_file(), {"fetched_at": time.time(), "payload": payload})
    except OSError as e:
        logger.warning(f"No se pudo escribir el caché de odds: {e}")


def _read_budget_counts() -> dict:
    """{"AAAA-MM": {key_hash: {"used": int, "provider_used": int|None,
    "provider_remaining": int|None}}} -- por (mes, key), nunca la key en
    texto plano (ver _hash_key)."""
    path = _budget_file()
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _hash_key(key: str) -> str:
    """Hash corto (8 hex de sha256) de una API key -- identifica su
    entrada en el JSON de presupuesto en disco SIN guardar la key
    completa ahí (ese archivo se sube como artifact/log de GitHub
    Actions). Nunca reversible a la key real, solo sirve para diferenciar
    entradas entre sí."""
    return hashlib.sha256(key.encode()).hexdigest()[:8]


def _check_budget_available(key: str) -> bool:
    """
    Solo LEE el contador de llamadas reales hechas este mes PARA ESTA key
    -- no reserva ni incrementa nada. Devuelve False si esta key ya agotó
    su presupuesto mensual (cada key tiene su propio contador -- rotar a
    otra key con presupuesto disponible es responsabilidad del caller,
    ver fetch_moneyline_odds). Separado de _record_budget_usage() a
    propósito: antes, el contador se incrementaba ANTES de saber si la
    llamada HTTP iba a tener éxito, así que una llamada fallida (timeout,
    500, red caída) consumía presupuesto igual que una exitosa -- una
    fuga real de cuota en la API más limitada de todas las que usa el
    proyecto.
    """
    month_key = date.today().strftime("%Y-%m")
    key_hash = _hash_key(key)
    used = _read_budget_counts().get(month_key, {}).get(key_hash, {}).get("used", 0)
    if used >= ODDS_API_MONTHLY_BUDGET:
        logger.warning(
            f"Presupuesto mensual de The Odds API agotado para la key {key_hash} "
            f"({used}/{ODDS_API_MONTHLY_BUDGET} en {month_key}) -- se prueba la siguiente "
            f"key configurada, si hay."
        )
        return False
    return True


def _record_budget_usage(key: str, provider_used: int | None = None,
                          provider_remaining: int | None = None) -> None:
    """Incrementa el contador local de ESTA key -- llamar SOLO después de
    una respuesta HTTP exitosa (ver fetch_moneyline_odds).

    provider_used/provider_remaining: los headers reales de The Odds API
    (x-requests-used/x-requests-remaining) si la respuesta los trajo --
    más confiables que el conteo local (que es una aproximación, según su
    propio criterio de diseño), se guardan junto al contador local para
    poder auditar la diferencia, y permiten advertir cuando el PROVEEDOR
    reporta poco margen aunque el conteo local todavía no lo refleje."""
    path = _budget_file()
    month_key = date.today().strftime("%Y-%m")
    key_hash = _hash_key(key)
    counts = _read_budget_counts()
    month_counts = counts.get(month_key, {})
    entry = month_counts.get(key_hash, {})
    used = entry.get("used", 0) + 1
    month_counts[key_hash] = {
        "used": used,
        "provider_used": provider_used,
        "provider_remaining": provider_remaining,
    }
    counts[month_key] = month_counts
    try:
        _atomic_write_json(path, counts)
    except OSError as e:
        logger.warning(f"No se pudo actualizar el contador de presupuesto de odds ({key_hash}): {e}")

    if used >= ODDS_API_MONTHLY_BUDGET * 0.8:
        logger.warning(
            f"Cerca del límite mensual de The Odds API para la key {key_hash}: "
            f"{used}/{ODDS_API_MONTHLY_BUDGET} (conteo local)."
        )

    if provider_remaining is not None:
        provider_total = provider_remaining + (provider_used or 0)
        if provider_total > 0 and provider_remaining <= provider_total * 0.2:
            logger.warning(
                f"The Odds API reporta poco margen para la key {key_hash}: "
                f"quedan {provider_remaining} requests según el proveedor (no el conteo local)."
            )


def _parse_payload(payload) -> list[dict]:
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

            raw_price = {
                "book": book.get("key"),
                "away_price": away_price,
                "home_price": home_price,
                "last_update": h2h.get("last_update") or book.get("last_update"),
            }
            gated = gate_quote(raw_price)
            if gated is None:
                logger.warning(f"Cuota descartada por esquema/rango inválido: {raw_price}")
                continue
            # fresh=False no se descarta aquí -- se conserva para CLV (ver
            # record_closing_odds), pero best_available_price()/
            # consensus_no_vig_prob() la excluyen para generar picks: una
            # cuota vieja (ej. tras un scratch de pitcher) siempre "parece"
            # tener valor, porque el mercado ya incorporó información que
            # este snapshot no tiene.
            raw_price["fresh"] = gated.fresh
            prices.append(raw_price)

        events.append({
            "home_team": home_team,
            "away_team": away_team,
            "commence_time": commence_time,
            "prices": prices,
        })

    return events


def _try_key(key: str, key_label: str) -> list | None:
    """Un solo intento con UNA key: None si hay que probar la siguiente
    (presupuesto agotado, 401/429, o falla de red) -- nunca lanza. El
    caller (fetch_moneyline_odds) decide qué hacer si todas fallan."""
    if not _check_budget_available(key):
        return None  # ya logueado dentro de _check_budget_available

    params = {
        "apiKey": key,
        "regions": "us",
        "markets": "h2h",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    try:
        resp = requests.get(ODDS_API_BASE, params=params, timeout=15)
        if resp.status_code in (401, 429):
            logger.warning(
                f"The Odds API rechazó la key {key_label} (HTTP {resp.status_code}) -- "
                f"se prueba la siguiente key configurada, si hay."
            )
            return None
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as e:
        logger.warning(
            f"La key {key_label} de The Odds API falló: {_sanitize(e)} -- "
            f"se prueba la siguiente key configurada, si hay."
        )
        return None

    provider_remaining = resp.headers.get("x-requests-remaining")
    provider_used = resp.headers.get("x-requests-used")
    _record_budget_usage(
        key,
        provider_used=int(provider_used) if provider_used and provider_used.isdigit() else None,
        provider_remaining=int(provider_remaining) if provider_remaining and provider_remaining.isdigit() else None,
    )
    _write_cache(payload)
    return payload


def fetch_moneyline_odds() -> list[dict]:
    """
    Trae las cuotas moneyline (h2h) actuales de todos los bookmakers
    disponibles en la región US. Usa caché con TTL antes que golpear la
    API, y rota entre las keys configuradas (ver
    config.resolve_odds_api_keys) -- cada una con su propio presupuesto
    mensual; se prueba la siguiente si la actual ya agotó su presupuesto,
    responde 401/429, o falla por red. Si TODAS fallan o están agotadas,
    se degrada al último caché conocido (aunque esté vencido) antes que
    quedarse sin nada. Devuelve [] solo si no hay ninguna key configurada,
    o ninguna key funcionó y no hay ningún caché previo disponible.
    """
    api_keys = resolve_odds_api_keys()
    if not api_keys:
        logger.info("ODDS_API_KEY/ODDS_API_KEYS no configuradas — se omite la consulta de cuotas en vivo.")
        _last_fetch_meta.update(source="none", fetched_at=None)
        return []

    cached = _read_cache()
    if cached is not None:
        _last_fetch_meta.update(source="api_cache", fetched_at=_cache_fetched_at())
        return _parse_payload(cached)

    for idx, key in enumerate(api_keys):
        key_label = f"#{idx + 1} ({_hash_key(key)})"
        payload = _try_key(key, key_label)
        if payload is not None:
            _last_fetch_meta.update(source="api_live", fetched_at=_cache_fetched_at())
            return _parse_payload(payload)

    stale = _read_cache(ignore_ttl=True)
    if stale is not None:
        logger.warning("Usando el último caché de odds conocido (vencido) -- ninguna key configurada funcionó.")
        _last_fetch_meta.update(source="api_stale_cache", fetched_at=_cache_fetched_at())
        return _parse_payload(stale)
    _last_fetch_meta.update(source="none", fetched_at=None)
    return []


def match_odds_to_game(events: list[dict], away_team: str, home_team: str,
                        game_datetime_iso: str | None = None) -> dict | None:
    """
    Empareja un juego de la MLB Stats API contra los eventos de The Odds
    API por nombre de equipo (normalizado). None si no hay match.

    En un doubleheader (mismos dos equipos, mismo día, dos juegos), puede
    haber más de un evento con el mismo nombre de equipos -- sin
    `game_datetime_iso` se devuelve el primero (comportamiento anterior,
    compatible hacia atrás), lo cual le asignaría al juego 2 las cuotas del
    juego 1. Si se pasa `game_datetime_iso` (el `game_time`/gameDate real
    del juego que viene de la MLB Stats API), se desambigua eligiendo el
    evento cuyo commence_time esté más cerca de esa fecha/hora real.
    """
    away_norm = _normalize_team_name(away_team)
    home_norm = _normalize_team_name(home_team)
    matches = [
        event for event in events
        if _normalize_team_name(event["away_team"]) == away_norm
        and _normalize_team_name(event["home_team"]) == home_norm
    ]

    if len(matches) <= 1 or not game_datetime_iso:
        return matches[0] if matches else None

    try:
        target = datetime.fromisoformat(game_datetime_iso.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return matches[0]

    def _distance(event: dict) -> timedelta:
        commence_time = event.get("commence_time")
        if not commence_time:
            return timedelta(days=99)
        try:
            ct = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return timedelta(days=99)
        return abs(ct - target)

    return min(matches, key=_distance)


def _fresh_prices(event: dict) -> list[dict]:
    """
    Filtra a las cuotas frescas (ver data/quote_gate.py) -- una cuota vieja
    no debe alimentar ni el consenso ni la mejor cuota disponible, porque
    en edge-hunting una línea vieja siempre "parece" tener valor. Los
    precios que no pasaron por _parse_payload() (ej. dicts construidos a
    mano en tests, o por callers directos) no tienen la clave "fresh" --
    se tratan como frescas por defecto, no penalizamos la ausencia del dato.
    """
    return [p for p in event["prices"] if p.get("fresh", True)]


def consensus_no_vig_prob(event: dict) -> tuple[float, float] | None:
    """
    Promedia la probabilidad sin vig de cada bookmaker disponible (y
    fresco) para este evento — el consenso "justo" del mercado, usado como
    referencia para medir edge real y Closing Line Value. None si ningún
    bookmaker trajo datos usables y frescos para este evento.
    """
    from model.edge import no_vig_probs

    fresh = _fresh_prices(event)
    if not fresh:
        return None

    away_probs, home_probs = [], []
    for p in fresh:
        away_p, home_p = no_vig_probs(p["away_price"], p["home_price"])
        away_probs.append(away_p)
        home_probs.append(home_p)

    return sum(away_probs) / len(away_probs), sum(home_probs) / len(home_probs)


def consensus_power_devig_prob(event: dict) -> tuple[float, float] | None:
    """
    M4: mismo consenso que consensus_no_vig_prob(), pero con el método de
    devig "power" (model.edge.power_devig) en vez del proporcional --
    referencia SECUNDARIA para comparación futura, congelada en el
    snapshot (market_no_vig_power). No reemplaza a consensus_no_vig_prob()
    en ninguna decisión real del pipeline (CLV/edge/picks siguen usando
    el consenso proporcional). None si ningún bookmaker trajo datos
    usables y frescos para este evento.
    """
    from model.edge import power_devig

    fresh = _fresh_prices(event)
    if not fresh:
        return None

    away_probs, home_probs = [], []
    for p in fresh:
        away_p, home_p = power_devig(p["away_price"], p["home_price"])
        away_probs.append(away_p)
        home_probs.append(home_p)

    return sum(away_probs) / len(away_probs), sum(home_probs) / len(home_probs)


def best_available_price(event: dict) -> dict | None:
    """
    La mejor cuota tomable por lado, entre las frescas (la que de verdad
    podrías apostar) — distinta del consenso no-vig, que sirve para medir
    edge/CLV, no para ejecutar la apuesta. None si no hay bookmakers con
    datos usables y frescos.
    """
    fresh = _fresh_prices(event)
    if not fresh:
        return None

    best_away = max(p["away_price"] for p in fresh)
    best_home = max(p["home_price"] for p in fresh)
    return {"away": best_away, "home": best_home}
