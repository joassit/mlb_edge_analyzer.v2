"""Lesiones (IL) en vivo via `statsapi.mlb.com/api/v1/transactions` --
misma fuente y logica de parseo que `jsa/historical/injuries.py`, pero
DUPLICADA a proposito en vez de importada: produccion nunca depende de
`jsa.historical` (ver `jsa/tests/test_production_isolation.py`).

Mismo patron que `travel.py`/`weather.py`: se trae la temporada COMPLETA
de transacciones (hasta HOY) en una unica llamada de red por corrida
(`build_today_injury_index`, llamado UNA vez en `main.py`, nunca por
juego) -- el unico costo de red adicional es, igual que en
`jsa/historical/injuries.py`, una consulta por JUGADOR actualmente en IL
(para evaluar su PA/IP reciente y decidir si es "lesion clave"), nunca por
juego."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import requests

from jsa.config import MLB_API_BASE, SEASON
from jsa.data_sources.http import session

logger = logging.getLogger("jsa")

# Mismo umbral acordado que la version historica (ver
# `jsa/historical/injuries.py`): bateador con >=50 PA o pitcher con >=15
# IP en los 30 dias previos a su colocacion en IL se considera "lesion
# clave".
KEY_INJURY_MIN_HITTER_PA = 50
KEY_INJURY_MIN_PITCHER_IP = 15
KEY_INJURY_LOOKBACK_DAYS = 30

_PLACED_RE = re.compile(r"\bplaced\b.*\bon the\b.*\binjured list\b", re.IGNORECASE)
_ACTIVATED_RE = re.compile(r"\bactivated\b.*\bfrom the\b.*\binjured list\b", re.IGNORECASE)


def _parse_innings(ip_str: str) -> float:
    """'63.1'/'63.2' son 63 y 1/3 / 2/3 entradas, no base 10."""
    if "." not in ip_str:
        return float(ip_str)
    whole, frac = ip_str.split(".")
    thirds = {"0": 0, "1": 1, "2": 2}.get(frac, 0)
    return int(whole) + thirds / 3


def fetch_season_transactions(season: int = SEASON) -> list[dict]:
    """Todas las transacciones de la temporada hasta HOY -- una sola
    llamada de red. Devuelve [] si la API falla, nunca propaga."""
    start_date = f"{season}-03-01"
    end_date = date.today().isoformat()
    try:
        resp = session.get(
            f"{MLB_API_BASE}/transactions",
            params={"startDate": start_date, "endDate": end_date, "sportId": 1},
            timeout=15,
        )
        resp.raise_for_status()
        transactions = resp.json().get("transactions", [])
    except (requests.RequestException, ValueError) as e:
        logger.warning("No se pudo obtener transacciones de la temporada %s: %s", season, e)
        return []
    logger.info("Temporada %s: %d transacciones encontradas (%s a %s).", season, len(transactions), start_date, end_date)
    return transactions


@dataclass
class ILEvent:
    player_id: int
    player_name: str
    team_id: int
    event_date: str  # `date` (fecha de anuncio publico), nunca `effectiveDate`
    kind: str  # "placed" | "activated"
    is_pitcher: bool


def parse_il_events(transactions: list[dict]) -> list[ILEvent]:
    """Puro -- misma clasificacion via regex sobre `description` que
    `jsa/historical/injuries.py::parse_il_events`. Ignora "transferred"
    (el jugador sigue lesionado, solo cambia de tier de IL)."""
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
            continue

        is_pitcher = bool(re.search(r"\b(RHP|LHP|P)\b", description))
        events.append(ILEvent(
            player_id=player_id, player_name=person.get("fullName", "?"), team_id=team_id,
            event_date=event_date, kind=kind, is_pitcher=is_pitcher,
        ))
    return events


def _recent_hitter_pa(player_id: int, before_date: str, days: int = KEY_INJURY_LOOKBACK_DAYS) -> int | None:
    start = (date.fromisoformat(before_date) - timedelta(days=days)).isoformat()
    end = (date.fromisoformat(before_date) - timedelta(days=1)).isoformat()
    try:
        params = {"stats": "byDateRange", "group": "hitting", "startDate": start, "endDate": end}
        resp = session.get(f"{MLB_API_BASE}/people/{player_id}/stats", params=params, timeout=15)
        resp.raise_for_status()
        splits = resp.json()["stats"][0]["splits"]
        if not splits:
            return None
        pa = splits[0]["stat"].get("plateAppearances")
        return int(pa) if pa is not None else None
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.debug("PA reciente fallo para jugador %s: %s", player_id, e)
        return None


def _recent_pitcher_ip(player_id: int, before_date: str, days: int = KEY_INJURY_LOOKBACK_DAYS) -> float | None:
    start = (date.fromisoformat(before_date) - timedelta(days=days)).isoformat()
    end = (date.fromisoformat(before_date) - timedelta(days=1)).isoformat()
    try:
        params = {"stats": "byDateRange", "group": "pitching", "startDate": start, "endDate": end}
        resp = session.get(f"{MLB_API_BASE}/people/{player_id}/stats", params=params, timeout=15)
        resp.raise_for_status()
        splits = resp.json()["stats"][0]["splits"]
        if not splits:
            return None
        ip_str = splits[0]["stat"].get("inningsPitched")
        return _parse_innings(str(ip_str)) if ip_str is not None else None
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.debug("IP reciente fallo para jugador %s: %s", player_id, e)
        return None


@dataclass
class InjuryIndex:
    events_by_player: dict[int, list[tuple[str, str]]] = field(default_factory=dict)
    name_by_player: dict[int, str] = field(default_factory=dict)
    # Misma limitacion aceptada que la version historica: un traspaso a
    # mitad de temporada deja al jugador indexado bajo su equipo mas
    # reciente para todos sus eventos, no el equipo real al momento de
    # cada evento.
    team_by_player: dict[int, int] = field(default_factory=dict)
    is_key_by_player: dict[int, bool] = field(default_factory=dict)


def build_injury_index(il_events: list[ILEvent]) -> InjuryIndex:
    """Unica funcion de este modulo que pega la red mas alla del fetch
    inicial de transacciones -- una vez POR JUGADOR con al menos un evento
    "placed" (nunca por juego)."""
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
            ip = _recent_pitcher_ip(player_id, first_placement)
            index.is_key_by_player[player_id] = ip is not None and ip >= KEY_INJURY_MIN_PITCHER_IP
        else:
            pa = _recent_hitter_pa(player_id, first_placement)
            index.is_key_by_player[player_id] = pa is not None and pa >= KEY_INJURY_MIN_HITTER_PA

    return index


def is_injured_as_of(index: InjuryIndex, player_id: int | None, as_of_date: str) -> bool:
    """Mismo criterio que la version historica: solo cuentan eventos
    ESTRICTAMENTE anteriores a `as_of_date` -- una colocacion en IL
    anunciada el mismo dia del juego todavia no cuenta para ese juego."""
    if player_id is None or player_id not in index.events_by_player:
        return False
    injured = False
    for event_date, kind in index.events_by_player[player_id]:
        if event_date >= as_of_date:
            break
        injured = kind == "placed"
    return injured


def key_injuries_as_of(index: InjuryIndex, team_id: int, as_of_date: str) -> list[str]:
    names: list[str] = []
    for player_id, team in index.team_by_player.items():
        if team != team_id or not index.is_key_by_player.get(player_id, False):
            continue
        if is_injured_as_of(index, player_id, as_of_date):
            names.append(index.name_by_player[player_id])
    return names


def build_today_injury_index(season: int = SEASON) -> InjuryIndex:
    """Punto de entrada -- se llama UNA vez por corrida diaria en
    `main.py`, igual que `build_league_context`, nunca por juego."""
    il_events = parse_il_events(fetch_season_transactions(season))
    return build_injury_index(il_events)
