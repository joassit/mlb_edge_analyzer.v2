"""Spike de factibilidad de Statcast -- Etapa 1 del plan acordado con el
usuario (ver `jsa/docs/statcast_integration_design.md`, seccion "Proximo
paso"). Responde 4 preguntas concretas, SIN tocar el modelo ni persistir
nada en ninguna base de JSA:

1. ¿Hay acceso real a Baseball Savant desde donde corre esto (GitHub
   Actions, no el sandbox de desarrollo -- ese ya confirmo bloqueo de
   proxy contra `statsapi.mlb.com` y se asume el mismo bloqueo aca)?
2. ¿Hay cobertura para las 5 temporadas objetivo (2022-2026)?
3. ¿Cuales son los tiempos de respuesta, limites y estabilidad?
4. ¿Se puede reconstruir de forma point-in-time sin leakage (el filtro
   de fecha realmente excluye datos posteriores al corte)?

IMPORTANTE: los endpoints/parametros de Baseball Savant usados aca NO son
una API oficial documentada -- son los mismos que usa la comunidad
sabermetrica publica (ej. la libreria `pybaseball`) para consultar el
mismo buscador que expone el sitio web. Este script prueba varios
candidatos y reporta que funciono de verdad, no asume que un parametro
es correcto de antemano. Ver Seccion 3 de `statcast_integration_design.md`.

Este script es descartable -- no se importa desde ningun otro modulo de
jsa/, no escribe en ninguna base de datos, solo hace GET HTTP y reporta
JSON. Correr con: `python -m jsa.historical.spikes.statcast_feasibility_spike`
"""

from __future__ import annotations

import csv
import io
import json
import logging
import sys
import time
from datetime import date, datetime, timezone

import requests

logger = logging.getLogger("jsa.historical.spikes.statcast")

BASE = "https://baseballsavant.mlb.com"
REQUEST_TIMEOUT = 30
TARGET_SEASONS = (2022, 2023, 2024, 2025, 2026)
PROBE_TEAM = "NYY"  # equipo fijo, arbitrario, solo para probar forma de la respuesta


def _statcast_search_csv_params(*, season: int, game_date_gt: str, game_date_lt: str, team: str) -> dict:
    """Parametros del buscador de Statcast a nivel evento (pitch-by-pitch),
    reconstruidos de uso publico conocido (nunca documentados oficialmente
    por MLB) -- devuelve datos crudos con `game_date`, `launch_speed`,
    `launch_angle`, `estimated_woba_using_speedangle`, etc. por lanzamiento/
    bateo, de los cuales se agregaria manualmente un xwOBA de equipo por
    ventana de fechas (mismo patron que `team_ops_rolling_as_of()`)."""
    return {
        "all": "true",
        "hfGT": "R|",
        "hfSea": f"{season}|",
        "player_type": "batter",
        "game_date_gt": game_date_gt,
        "game_date_lt": game_date_lt,
        "team": team,
        "min_pitches": "0",
        "min_results": "0",
        "group_by": "name",
        "sort_col": "pitches",
        "player_event_type": "batter",
        "sort_order": "desc",
        "min_pas": "0",
        "type": "details",
    }


def _probe(name: str, url: str, params: dict) -> dict:
    entry = {"name": name, "url": url, "params": params}
    start = time.monotonic()
    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "jsa-feasibility-spike/1.0"})
        elapsed = time.monotonic() - start
        entry.update({
            "status_code": resp.status_code,
            "elapsed_seconds": round(elapsed, 3),
            "content_type": resp.headers.get("content-type"),
            "content_length": len(resp.content),
            "rate_limit_headers": {k: v for k, v in resp.headers.items() if "rate" in k.lower() or "retry" in k.lower()},
        })
        if resp.status_code == 200 and resp.content:
            text = resp.text
            entry["body_sample_first_500_chars"] = text[:500]
            try:
                reader = csv.DictReader(io.StringIO(text))
                rows = list(reader)
                entry["parsed_as_csv"] = True
                entry["n_rows"] = len(rows)
                entry["columns"] = reader.fieldnames
                if rows and "game_date" in (reader.fieldnames or []):
                    dates = sorted({r["game_date"] for r in rows if r.get("game_date")})
                    entry["min_game_date_in_response"] = dates[0] if dates else None
                    entry["max_game_date_in_response"] = dates[-1] if dates else None
            except Exception as e:  # noqa: BLE001 -- spike, capturar cualquier fallo de parseo y reportarlo
                entry["parsed_as_csv"] = False
                entry["csv_parse_error"] = str(e)
        else:
            entry["body_sample_first_500_chars"] = resp.text[:500] if resp.content else None
    except requests.RequestException as e:
        entry["error"] = str(e)
        entry["elapsed_seconds"] = round(time.monotonic() - start, 3)
    return entry


def probe_endpoint_candidates() -> list[dict]:
    """Pregunta 1 y 3: ¿hay acceso real, y que forma tiene la respuesta?
    Prueba el endpoint de busqueda a nivel evento (el mas util para
    reconstruccion point-in-time) y dos leaderboards agregados de
    temporada completa (utiles solo como contexto -- probablemente NO
    soporten sub-rangos de fecha, se confirma aca mismo)."""
    probes = []
    probes.append(_probe(
        "statcast_search_csv_1week",
        f"{BASE}/statcast_search/csv",
        _statcast_search_csv_params(season=2023, game_date_gt="2023-04-01", game_date_lt="2023-04-08", team=PROBE_TEAM),
    ))
    probes.append(_probe(
        "leaderboard_expected_statistics_season",
        f"{BASE}/leaderboard/expected_statistics",
        {"type": "batter", "year": "2023", "position": "", "team": "", "filterType": "bip", "min": "q", "csv": "true"},
    ))
    probes.append(_probe(
        "leaderboard_custom_season",
        f"{BASE}/leaderboard/custom",
        {"year": "2023", "type": "batter", "min": "q", "selections": "xwoba,barrel_batted_rate,hard_hit_percent", "chart": "false", "csv": "true"},
    ))
    return probes


def probe_season_coverage() -> dict:
    """Pregunta 2: ¿hay datos para las 5 temporadas objetivo? Una ventana
    corta de 1 semana en abril de cada temporada (early-season, mismo
    tipo de fecha que ya se uso para el resto del proyecto) -- para 2026
    (temporada en curso) se usa una ventana reciente en vez de abril
    fijo, para no asumir que ya paso esa fecha."""
    coverage = {}
    for season in TARGET_SEASONS:
        if season == max(TARGET_SEASONS):
            game_date_gt, game_date_lt = f"{season}-06-01", f"{season}-06-08"
        else:
            game_date_gt, game_date_lt = f"{season}-04-01", f"{season}-04-08"
        result = _probe(
            f"coverage_{season}",
            f"{BASE}/statcast_search/csv",
            _statcast_search_csv_params(season=season, game_date_gt=game_date_gt, game_date_lt=game_date_lt, team=PROBE_TEAM),
        )
        coverage[str(season)] = result
    return coverage


def probe_point_in_time_integrity() -> dict:
    """Pregunta 4, la mas critica: ¿el filtro de fecha realmente excluye
    datos posteriores al corte? Se pide la MISMA ventana con dos cortes
    (`game_date_lt`) distintos, una semana de diferencia, y se compara:
    (a) la fecha MAXIMA devuelta en cada respuesta debe ser estrictamente
    anterior a su propio `game_date_lt` (nunca >=) -- si no, hay leakage
    real en la fuente misma, no en nuestro codigo.
    (b) el corte mas tardio debe devolver un superset (mismo o mas datos)
    que el corte mas temprano."""
    season = 2023
    cutoff_a, cutoff_b = "2023-04-07", "2023-04-14"
    probe_a = _probe(
        "pit_check_cutoff_a",
        f"{BASE}/statcast_search/csv",
        _statcast_search_csv_params(season=season, game_date_gt="2023-04-01", game_date_lt=cutoff_a, team=PROBE_TEAM),
    )
    probe_b = _probe(
        "pit_check_cutoff_b",
        f"{BASE}/statcast_search/csv",
        _statcast_search_csv_params(season=season, game_date_gt="2023-04-01", game_date_lt=cutoff_b, team=PROBE_TEAM),
    )

    max_date_a = probe_a.get("max_game_date_in_response")
    max_date_b = probe_b.get("max_game_date_in_response")
    n_rows_a = probe_a.get("n_rows")
    n_rows_b = probe_b.get("n_rows")

    leakage_a = bool(max_date_a and max_date_a >= cutoff_a)
    leakage_b = bool(max_date_b and max_date_b >= cutoff_b)
    superset_ok = (n_rows_a is not None and n_rows_b is not None and n_rows_b >= n_rows_a)

    return {
        "cutoff_a": cutoff_a, "cutoff_b": cutoff_b,
        "probe_a": probe_a, "probe_b": probe_b,
        "max_game_date_a": max_date_a, "max_game_date_b": max_date_b,
        "n_rows_a": n_rows_a, "n_rows_b": n_rows_b,
        "leakage_detected_in_cutoff_a": leakage_a,
        "leakage_detected_in_cutoff_b": leakage_b,
        "later_cutoff_returns_superset": superset_ok,
        "point_in_time_safe": (not leakage_a) and (not leakage_b) and superset_ok,
    }


def run_spike() -> dict:
    logger.info("Statcast feasibility spike -- iniciando")
    result = {
        "spike_metadata": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "target_seasons": list(TARGET_SEASONS),
            "probe_team": PROBE_TEAM,
            "note": "endpoints/parametros son candidatos reconstruidos de uso publico conocido, NO una API oficial documentada -- ver statcast_integration_design.md",
        },
        "endpoint_probes": probe_endpoint_candidates(),
        "coverage_by_season": probe_season_coverage(),
        "point_in_time_check": probe_point_in_time_integrity(),
    }
    logger.info("Statcast feasibility spike completo")
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    output = run_spike()
    text = json.dumps(output, indent=2, default=str)
    print(text)
    if "--out" in sys.argv:
        out_path = sys.argv[sys.argv.index("--out") + 1]
        with open(out_path, "w") as f:
            f.write(text)
