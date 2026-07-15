"""Lesiones (IL) point-in-time via `statsapi.mlb.com/api/v1/transactions`
-- fuente validada por un spike de investigacion real (cobertura
confirmada en las 5 temporadas 2022-2026, ver PR de esta entrega) en vez
de recurrir a scraping de Pro Sports Transactions.

Mismo principio que `ingestion.py::build_previous_park_index()`: se trae
la temporada COMPLETA de transacciones en una sola llamada de red
(`fetch_season_transactions`, mismo patron que `fetch_season_games`), y
todo lo demas (parseo, filtro de "lesion clave", consulta point-in-time
por juego) se calcula en memoria -- **cero llamadas de red adicionales
durante el loop principal de ingesta**, salvo una excepcion deliberada:
`build_key_injuries_index()` SI pega la red, pero una unica vez POR
JUGADOR lesionado en toda la temporada (para evaluar su PA/IP reciente al
momento de la lesion), nunca por juego."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import requests

from jsa.historical.config import INGESTION_REQUEST_TIMEOUT, MLB_API_BASE
from jsa.historical.ingestion import season_date_range
from jsa.historical.point_in_time_provider import HistoricalStatsProvider
from jsa.data_sources.http import session

logger = logging.getLogger("jsa.historical")

# Umbral acordado: bateador con >=50 PA o pitcher con >=15 IP en los 30
# dias previos a su colocacion en IL se considera "lesion clave". Punto de
# partida (mismo espiritu que SMALL_SAMPLE_OFFENSE_PA en config.py), no
# calibrado todavia -- ajustable una vez que haya resultados reales.
KEY_INJURY_MIN_HITTER_PA = 50
KEY_INJURY_MIN_PITCHER_IP = 15
KEY_INJURY_LOOKBACK_DAYS = 30

_PLACED_RE = re.compile(r"\bplaced\b.*\bon the\b.*\binjured list\b", re.IGNORECASE)
_ACTIVATED_RE = re.compile(r"\bactivated\b.*\bfrom the\b.*\binjured list\b", re.IGNORECASE)


def fetch_season_transactions(season: int) -> list[dict]:
    """Todas las transacciones de la temporada (rango identico a
    `fetch_season_games`) -- una sola llamada de red. Devuelve [] si la API
    falla, nunca propaga (misma disciplina que el resto del modulo)."""
    start_date, end_date = season_date_range(season)
    try:
        resp = session.get(
            f"{MLB_API_BASE}/transactions",
            params={"startDate": start_date, "endDate": end_date, "sportId": 1},
            timeout=INGESTION_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        transactions = resp.json().get("transactions", [])
    except (requests.RequestException, ValueError) as e:
        logger.warning("No se pudo obtener transacciones de la temporada %s (%s a %s): %s", season, start_date, end_date, e)
        return []
    logger.info("Temporada %s: %d transacciones encontradas (%s a %s).", season, len(transactions), start_date, end_date)
    return transactions


@dataclass
class ILEvent:
    player_id: int
    player_name: str
    team_id: int
    event_date: str  # `date` (fecha de ANUNCIO publico), nunca `effectiveDate` (puede ser retroactivo -- ver ROADMAP)
    kind: str  # "placed" | "activated"
    is_pitcher: bool


def parse_il_events(transactions: list[dict]) -> list[ILEvent]:
    """Puro -- clasifica cada transaccion via regex sobre `description`
    (el unico campo confiable: `typeCode` es siempre "SC"/Status Change
    para movimientos de IL, confirmado en el spike). Ignora "transferred"
    (el jugador sigue lesionado, solo cambia de tier de IL -- no hay
    cambio de estado que registrar)."""
    events: list[ILEvent] = []
    for t in transactions:
        description = t.get("description") or ""
        person = t.get("person") or {}
        to_team = t.get("toTeam") or {}
        player_id, team_id, event_date = person.get("id"), to_team.get("id"), t.get("date")
        if player_id is None or team_id is None or event_date is None:
            continue

        if _ACTIVATED_RE.search(description):
            kind = "activated"
        elif _PLACED_RE.search(description):
            kind = "placed"
        else:
            continue  # "transferred" u otro tipo de transaccion -- no cambia disponibilidad

        is_pitcher = bool(re.search(r"\b(RHP|LHP|P)\b", description))
        events.append(ILEvent(
            player_id=player_id, player_name=person.get("fullName", "?"), team_id=team_id,
            event_date=event_date, kind=kind, is_pitcher=is_pitcher,
        ))
    return events


@dataclass
class InjuryIndex:
    """Estructura en memoria, construida UNA vez por temporada -- todas
    las consultas point-in-time posteriores (`is_injured_as_of`,
    `key_injuries_as_of`) son puras, cero red."""

    events_by_player: dict[int, list[tuple[str, str]]] = field(default_factory=dict)  # player_id -> [(date, kind), ...] ordenado
    name_by_player: dict[int, str] = field(default_factory=dict)
    # Limitacion conocida (aceptada a proposito, ver KEY_INJURY_* arriba):
    # un jugador cambiado de equipo la MISMA temporada en la que tambien
    # se lesiono queda indexado bajo su equipo mas reciente para TODOS sus
    # eventos de esa temporada, no el equipo real al momento de cada
    # evento -- caso de baja probabilidad (jugador lesionado + traspasado
    # en la misma temporada), no vale la pena trackear equipo por evento
    # todavia.
    team_by_player: dict[int, int] = field(default_factory=dict)
    is_key_by_player: dict[int, bool] = field(default_factory=dict)


def build_injury_index(
    il_events: list[ILEvent],
    provider: HistoricalStatsProvider,
    *,
    min_hitter_pa: int = KEY_INJURY_MIN_HITTER_PA,
    min_pitcher_ip: float = KEY_INJURY_MIN_PITCHER_IP,
) -> InjuryIndex:
    """Unica funcion de este modulo que pega la red -- una vez POR JUGADOR
    con al menos un evento "placed" (nunca por juego): se evalua su PA/IP
    de los 30 dias previos a esa colocacion para decidir si es "clave"."""
    index = InjuryIndex()

    for ev in il_events:
        index.events_by_player.setdefault(ev.player_id, []).append((ev.event_date, ev.kind))
        index.name_by_player[ev.player_id] = ev.player_name
        index.team_by_player[ev.player_id] = ev.team_id

    for player_id, events in index.events_by_player.items():
        events.sort(key=lambda e: e[0])
        first_placement = next((e[0] for e in events if e[1] == "placed"), None)
        if first_placement is None:
            index.is_key_by_player[player_id] = False
            continue

        is_pitcher = any(e.is_pitcher for e in il_events if e.player_id == player_id)
        if is_pitcher:
            ip = provider.pitcher_recent_ip_as_of(player_id, first_placement, days=KEY_INJURY_LOOKBACK_DAYS)
            index.is_key_by_player[player_id] = ip is not None and ip >= min_pitcher_ip
        else:
            pa = provider.hitter_recent_pa_as_of(player_id, first_placement, days=KEY_INJURY_LOOKBACK_DAYS)
            index.is_key_by_player[player_id] = pa is not None and pa >= min_hitter_pa

    return index


def is_injured_as_of(index: InjuryIndex, player_id: int | None, as_of_date: str) -> bool:
    """Reproduce la linea de tiempo de eventos de UN jugador hasta (sin
    incluir) `as_of_date` y devuelve si el ultimo evento fue "placed" sin
    "activated" posterior. Usado para `closer_available` -- deliberadamente
    SIN filtro de "lesion clave" (un cerrador lesionado importa sin
    importar si cruza el umbral de PA/IP, que ademas no aplica bien a
    relevistas)."""
    if player_id is None or player_id not in index.events_by_player:
        return False
    injured = False
    for event_date, kind in index.events_by_player[player_id]:
        if event_date >= as_of_date:
            break
        injured = kind == "placed"
    return injured


def key_injuries_as_of(index: InjuryIndex, team_id: int, as_of_date: str) -> list[str]:
    """Nombres de jugadores "clave" (Seccion team_quality) del equipo
    todavia en IL a `as_of_date`."""
    names: list[str] = []
    for player_id, team in index.team_by_player.items():
        if team != team_id or not index.is_key_by_player.get(player_id, False):
            continue
        if is_injured_as_of(index, player_id, as_of_date):
            names.append(index.name_by_player[player_id])
    return names
