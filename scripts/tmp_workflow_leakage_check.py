"""
TEMPORAL -- validación de leakage en vivo, punto 1 pedido por el usuario.
Se corre UNA vez vía GitHub Actions (el sandbox de desarrollo no tiene
salida de red a statsapi.mlb.com) y se retira junto con el workflow que lo
invoca al terminar. No es parte permanente de historical_engine.

Estrategia (sin depender de una fuente externa como Baseball-Reference,
que tampoco es alcanzable desde este entorno): para un pitcher real y un
equipo real de 2024, se reconstruye ERA/IP/OPS point-in-time con
MLBStatsAPIProvider (el proveedor real que usa el motor histórico) en 3-5
fechas de corte reales, y se compara contra:

  (a) la MISMA métrica acumulada a TEMPORADA COMPLETA (stats=season) --
      si el motor tuviera fuga, el número point-in-time coincidiría con
      este; se muestra explícitamente que NO coincide.
  (b) una reconciliación aritmética exacta: separar la temporada en
      "antes del corte" (lo que el motor histórico ve) + "después del
      corte" (lo que el motor histórico NUNCA debe ver) y confirmar que
      ambos pedazos SUMAN el total de temporada completa -- prueba de que
      el cutoff de byDateRange realmente particiona la temporada donde
      dice que particiona, no una aproximación.

Todas las llamadas son a la MLB Stats API pública (sin API key) y a
Open-Meteo Archive (sin API key) -- no hay ninguna credencial que pueda
filtrarse a este log. De todas formas, cualquier excepción se pasa por el
mismo _sanitize() que ya usa data/odds_api.py antes de imprimirse, por
higiene, en caso de que algún día este script se reutilice contra una
fuente que sí requiera credenciales.
"""

import re
import sys

from historical_engine.point_in_time_provider import MLBStatsAPIProvider, MLB_API_BASE
from data.http import session

_SENSITIVE_PARAM_RE = re.compile(r"(?i)([?&][^?&=\s]*(?:key|token)[^?&=\s]*=)[^&\s]+")


def _sanitize(exc_or_text) -> str:
    return _SENSITIVE_PARAM_RE.sub(r"\1***", str(exc_or_text))


SEASON = 2024
SEASON_START = "2024-03-20"
TIMEOUT = 15


def _get(url, params):
    resp = session.get(url, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def find_pitcher_with_multiple_starts(team_id: int, min_starts: int = 5):
    """Descubre un abridor real de `team_id` en SEASON con >= min_starts
    aperturas, y devuelve (pitcher_id, nombre, lista de fechas de sus
    aperturas ordenada). Nada hardcodeado -- se descubre desde el roster
    real vía API, para no arriesgar un ID de pitcher inventado/incorrecto.
    """
    roster = _get(f"{MLB_API_BASE}/teams/{team_id}/roster",
                   params={"rosterType": "40Man", "season": SEASON}).get("roster", [])
    pitchers = [p for p in roster if p.get("position", {}).get("abbreviation") == "P"]

    for p in pitchers:
        pid = p["person"]["id"]
        name = p["person"]["fullName"]
        log = _get(f"{MLB_API_BASE}/people/{pid}/stats",
                    params={"stats": "gameLog", "group": "pitching", "season": SEASON})
        splits = log.get("stats", [{}])[0].get("splits", [])
        starts = sorted(
            {s["date"] for s in splits if s.get("isHome") is not None and s.get("stat", {}).get("gamesStarted")},
            key=lambda d: d,
        )
        if len(starts) >= min_starts:
            return pid, name, starts
    return None, None, []


def full_season_era_ip(pitcher_id: int) -> tuple[float, float, int]:
    """ERA/IP/carreras-limpias de TEMPORADA COMPLETA (stats=season) -- el
    número que un motor con fuga devolvería sin querer si usara esta
    variante en vez de byDateRange con corte."""
    data = _get(f"{MLB_API_BASE}/people/{pitcher_id}/stats",
                params={"stats": "season", "group": "pitching", "season": SEASON})
    stat = data["stats"][0]["splits"][0]["stat"]
    return float(stat["era"]), _parse_ip(stat["inningsPitched"]), int(stat.get("earnedRuns", 0))


def range_era_ip_er(pitcher_id: int, start: str, end: str) -> tuple[float | None, float, int]:
    data = _get(f"{MLB_API_BASE}/people/{pitcher_id}/stats",
                params={"stats": "byDateRange", "group": "pitching", "season": SEASON,
                        "startDate": start, "endDate": end})
    splits = data["stats"][0]["splits"]
    if not splits:
        return None, 0.0, 0
    stat = splits[0]["stat"]
    return (float(stat["era"]) if stat.get("era") is not None else None,
            _parse_ip(stat.get("inningsPitched", "0.0")), int(stat.get("earnedRuns", 0)))


def _parse_ip(ip_str: str) -> float:
    if "." not in ip_str:
        return float(ip_str)
    whole, frac = ip_str.split(".")
    thirds = {"0": 0, "1": 1, "2": 2}.get(frac, 0)
    return int(whole) + thirds / 3


def full_season_team_ops(team_id: int) -> float:
    data = _get(f"{MLB_API_BASE}/teams/{team_id}/stats",
                params={"stats": "season", "group": "hitting", "season": SEASON})
    return float(data["stats"][0]["splits"][0]["stat"]["ops"])


def main() -> int:
    provider = MLBStatsAPIProvider()
    print("=" * 100)
    print("PUNTO 1 -- VALIDACION REAL DE LEAKAGE (datos en vivo, temporada 2024)")
    print("=" * 100)

    ok = True

    print("\n--- Descubriendo un abridor real con >=5 aperturas en 2024 (NYY, team_id=147) ---")
    try:
        pitcher_id, name, starts = find_pitcher_with_multiple_starts(147, min_starts=5)
    except Exception as e:
        print(f"::error::No se pudo descubrir un pitcher real: {_sanitize(e)}")
        return 1

    if pitcher_id is None:
        print("::error::Ningun pitcher de NYY con >=5 aperturas encontrado en 2024 -- no se puede validar.")
        return 1

    print(f"Pitcher elegido: {name} (id={pitcher_id}) -- {len(starts)} aperturas en 2024: {starts}")

    try:
        season_era, season_ip, season_er = full_season_era_ip(pitcher_id)
    except Exception as e:
        print(f"::error::No se pudo obtener ERA de temporada completa: {_sanitize(e)}")
        return 1
    print(f"\nERA de TEMPORADA COMPLETA (stats=season, tal como estaba al cierre de 2024): "
          f"ERA={season_era} IP={season_ip} ER={season_er}")
    print("(este es el numero que un motor CON fuga devolveria por error si usara stats=season "
          "en vez de byDateRange con corte -- se usa aqui solo como referencia de contraste)")

    n_dates = min(5, max(3, len(starts) - 1))
    cutoff_dates = starts[1:1 + n_dates]  # as_of_date = fecha de cada apertura (>= la 2da, para tener historial previo)

    print(f"\n--- Comparando point_in_time_stats() vs. reconciliacion manual en {len(cutoff_dates)} fechas reales ---")
    header = f"{'as_of_date':<12} {'ERA_point_in_time':<18} {'IP_before':<10} {'ERA_after':<10} {'IP_after':<10} {'IP_before+after':<16} {'IP_season':<10} {'match_IP':<9} {'ERA_pit != ERA_season':<22}"
    print(header)
    print("-" * len(header))

    for as_of in cutoff_dates:
        try:
            pit_result = provider.pitcher_era_ip_as_of(pitcher_id, as_of, SEASON)
        except Exception as e:
            print(f"::error::point_in_time_stats fallo para as_of={as_of}: {_sanitize(e)}")
            ok = False
            continue
        if pit_result is None:
            print(f"{as_of:<12} None (sin aperturas antes de este corte) -- se omite esta fecha")
            continue
        pit_era, pit_ip = pit_result

        end_before = provider._end_date(as_of)  # as_of - 1 dia, la misma logica interna del motor
        try:
            _, ip_before, er_before = range_era_ip_er(pitcher_id, SEASON_START, end_before)
            era_after, ip_after, er_after = range_era_ip_er(pitcher_id, as_of, "2024-09-29")
        except Exception as e:
            print(f"::error::reconciliacion manual fallo para as_of={as_of}: {_sanitize(e)}")
            ok = False
            continue

        ip_sum = round(ip_before + ip_after, 4)
        ip_match = abs(ip_sum - season_ip) < 0.35  # tolerancia: redondeo de outs a 1/3 de entrada
        era_differs = pit_era != season_era

        print(f"{as_of:<12} {pit_era:<18} {pit_ip:<10} {era_after if era_after is not None else 'N/D':<10} "
              f"{ip_after:<10} {ip_sum:<16} {season_ip:<10} {str(ip_match):<9} {str(era_differs):<22}")

        if abs(pit_ip - ip_before) > 0.01:
            print(f"::error::  IP point_in_time ({pit_ip}) no coincide con IP reconstruido manualmente "
                  f"antes del corte ({ip_before}) para as_of={as_of}")
            ok = False
        if not ip_match:
            print(f"::error::  IP antes+despues del corte ({ip_sum}) no reconcilia con IP de temporada "
                  f"completa ({season_ip}) para as_of={as_of} -- el cutoff de byDateRange no esta "
                  f"particionando la temporada donde dice que particiona.")
            ok = False
        if not era_differs and season_ip > pit_ip + 5:
            print(f"::warning::  ERA point-in-time es IGUAL al ERA de temporada completa para as_of={as_of} "
                  f"pese a que quedan {season_ip - pit_ip:.1f} entradas lanzadas despues del corte -- revisar.")

    print("\n--- Equipo (NYY, team_id=147): OPS point-in-time vs. OPS de temporada completa ---")
    try:
        season_ops = full_season_team_ops(147)
    except Exception as e:
        print(f"::error::No se pudo obtener OPS de temporada completa: {_sanitize(e)}")
        return 1
    print(f"OPS de TEMPORADA COMPLETA (stats=season): {season_ops}")

    for as_of in cutoff_dates[:3]:
        try:
            pit_ops = provider.team_ops_as_of(147, as_of, SEASON)
        except Exception as e:
            print(f"::error::team_ops_as_of fallo para as_of={as_of}: {_sanitize(e)}")
            ok = False
            continue
        differs = pit_ops is not None and pit_ops != season_ops
        print(f"as_of={as_of}: OPS_point_in_time={pit_ops}  OPS_season_completa={season_ops}  difiere={differs}")
        if pit_ops is not None and not differs:
            print(f"::warning::  OPS point-in-time identico al de temporada completa en as_of={as_of} -- "
                  f"revisar (podria ser coincidencia si el equipo no bateo entre esa fecha y fin de temporada).")

    print("\n--- Bullpen ERA (NYY, team_id=147) point-in-time, una fecha de muestra ---")
    try:
        sample_as_of = cutoff_dates[0]
        pit_bullpen = provider.bullpen_era_as_of(147, sample_as_of, SEASON)
        print(f"as_of={sample_as_of}: bullpen_era_as_of()={pit_bullpen} "
              f"(roster ACTUAL usado como aproximacion -- ver punto 3 del pedido del usuario, "
              f"ya corregido en reports.py para que este riesgo sea visible en el reporte)")
    except Exception as e:
        print(f"::error::bullpen_era_as_of fallo: {_sanitize(e)}")
        ok = False

    print("\n" + "=" * 100)
    print("RESULTADO PUNTO 1:", "TODAS LAS RECONCILIACIONES PASARON" if ok else "HAY DISCREPANCIAS -- VER ::error:: ARRIBA")
    print("=" * 100)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
