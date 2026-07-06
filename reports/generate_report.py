"""
Genera el reporte diario en consola y opcionalmente lo exporta a CSV.

El reporte tiene siempre dos secciones, en este orden fijo:
  1. print_yesterday_review() -- revisión de las predicciones de ayer,
     partido por partido y mercado por mercado, contra el resultado real.
  2. print_report() -- las predicciones de hoy (todavía por jugarse).

La Sección 1 nunca se omite en silencio: si no hay datos de ayer (feriado,
sin juegos, o el tracking aún no tiene resultado real), imprime un mensaje
explícito en vez de saltarse la sección.
"""

from datetime import date
import csv
import os


# Mascota corta de cada equipo de MLB (30 equipos), para el formato de
# picks "Phillies ML" en vez de "Philadelphia Phillies ML". No existe un
# mapeo así en ningún otro módulo del proyecto (_normalize_team_name en
# data/odds_api.py solo baja a minúsculas para comparar, no acorta el
# nombre), así que vive aquí, junto a la única función que lo necesita.
_TEAM_SHORT_NAMES = {
    "Arizona Diamondbacks": "Diamondbacks",
    "Atlanta Braves": "Braves",
    "Athletics": "Athletics",
    "Baltimore Orioles": "Orioles",
    "Boston Red Sox": "Red Sox",
    "Chicago Cubs": "Cubs",
    "Chicago White Sox": "White Sox",
    "Cincinnati Reds": "Reds",
    "Cleveland Guardians": "Guardians",
    "Colorado Rockies": "Rockies",
    "Detroit Tigers": "Tigers",
    "Houston Astros": "Astros",
    "Kansas City Royals": "Royals",
    "Los Angeles Angels": "Angels",
    "Los Angeles Dodgers": "Dodgers",
    "Miami Marlins": "Marlins",
    "Milwaukee Brewers": "Brewers",
    "Minnesota Twins": "Twins",
    "New York Mets": "Mets",
    "New York Yankees": "Yankees",
    "Philadelphia Phillies": "Phillies",
    "Pittsburgh Pirates": "Pirates",
    "San Diego Padres": "Padres",
    "San Francisco Giants": "Giants",
    "Seattle Mariners": "Mariners",
    "St. Louis Cardinals": "Cardinals",
    "Tampa Bay Rays": "Rays",
    "Texas Rangers": "Rangers",
    "Toronto Blue Jays": "Blue Jays",
    "Washington Nationals": "Nationals",
}


def _short_team_name(full_name: str) -> str:
    """Mascota corta (ej. 'Phillies') a partir del nombre completo que trae
    MLB Stats API (ej. 'Philadelphia Phillies'). Si el nombre no está en el
    mapeo (naming nuevo, expansión futura, typo en la fuente), cae a la
    última palabra -- funciona para la mayoría de los casos, salvo mascotas
    de dos palabras, que es exactamente lo que el mapeo de arriba cubre."""
    if not full_name:
        return full_name
    if full_name in _TEAM_SHORT_NAMES:
        return _TEAM_SHORT_NAMES[full_name]
    return full_name.split()[-1]


def team_label(pick: dict, game: dict) -> str:
    """
    Etiqueta legible de un pick con el nombre del equipo en vez de
    "local"/"visitante" -- ej. "Phillies ML", "Braves RL -1.5", "Over 8.5".
    Aplica igual a picks reales y forzados (forced=True); el llamador
    decide si además anota "(forzado)" aparte -- esta función solo arma la
    etiqueta del mercado/selección, no toca el estado forced/edge/EV.

    `pick`: dict con "market" ("moneyline"/"run_line"/"totals"),
    "selection" ("home"/"away" en ML y RL, "over"/"under" en totales) y
    "line" (None en ML, la línea de RL/totales en los otros dos).
    `game`: dict con "away_team"/"home_team" (nombres completos).
    """
    market = pick["market"]
    selection = pick["selection"]

    if market == "moneyline":
        team = game["home_team"] if selection == "home" else game["away_team"]
        return f"{_short_team_name(team)} ML"

    if market == "run_line":
        team = game["home_team"] if selection == "home" else game["away_team"]
        line = pick.get("line") if pick.get("line") is not None else 1.5
        sign = "-" if selection == "home" else "+"
        return f"{_short_team_name(team)} RL {sign}{line:g}"

    if market == "totals":
        line = pick.get("line")
        label = "Over" if selection == "over" else "Under"
        return f"{label} {line:g}" if line is not None else label

    return f"{market} {selection}"


_MARKET_REVIEW_LABELS = {"moneyline": "ML", "run_line": "Handicap", "totals": "Totales"}
_OUTCOME_LABELS = {"win": "✅ ACERTÓ", "loss": "❌ FALLÓ", "push": "➖ PUSH"}
_PROB_SOURCE_LABELS = {"heuristic": "Heurístico", "skellam": "Skellam", "negbin": "Bin. Neg."}


def _format_win_rate(value: float | None) -> str:
    return f"{value:.1%}" if value is not None else "N/A"


def _format_roi(value: float | None) -> str:
    return f"{value:+.1%}" if value is not None else "N/A"


def _print_market_review_line(label: str, game: dict, market: str) -> None:
    pick = game["picks"].get(market)
    prefix = f"  {label:<9} ->"

    extra = None
    if market == "run_line":
        proy, real = game["proj_margin"], game["actual_margin"]
        extra = (f"margen (local-visitante) proy. {proy:+.1f} | real {real:+d}"
                 if proy is not None else f"margen real {real:+d}")
    elif market == "totals":
        proy, real = game["proj_total"], game["actual_total"]
        extra = (f"total proy. {proy:.1f} | real {real}"
                 if proy is not None else f"total real {real}")

    if pick is None:
        tail = "sin pick (sin cuotas cargadas para este mercado)"
        print(f"{prefix} {tail}" + (f"  -- {extra}" if extra else ""))
        return

    label_txt = team_label(pick, game)
    outcome = _OUTCOME_LABELS.get(pick["result"], pick["result"])
    forced_tag = "  (forzado, sin edge real)" if pick.get("forced") else ""
    conf = f"  (confianza {pick['model_prob']:.1%})" if pick.get("model_prob") is not None else ""

    line = f"{prefix} {label_txt}{conf}{forced_tag}  ->  {outcome}"
    if extra:
        line += f"  ({extra})"
    print(line)


def _print_daily_review_summary(review: dict) -> None:
    print("--- Resumen del día ---")
    for market, label in _MARKET_REVIEW_LABELS.items():
        perf = review["by_market"][market]
        real, forced = perf["real"], perf["forced"]
        if not real["n_picks"] and not forced["n_picks"]:
            print(f"{label:<9}: sin picks liquidados ayer")
            continue
        parts = []
        if real["n_picks"]:
            parts.append(f"reales={real['n_picks']} win_rate={_format_win_rate(real['win_rate'])} "
                          f"ROI={_format_roi(real['roi'])}")
        if forced["n_picks"]:
            parts.append(f"forzados={forced['n_picks']} win_rate={_format_win_rate(forced['win_rate'])} "
                          f"ROI={_format_roi(forced['roi'])}")
        print(f"{label:<9}: " + "  |  ".join(parts))

    if review.get("brier_score") is not None:
        print(f"Brier Score del día: {review['brier_score']:.4f}")
    print()


def print_yesterday_review(review: dict | None) -> None:
    """
    Sección 1 del reporte diario -- revisión de las predicciones de ayer
    contra el resultado real, partido por partido y mercado por mercado
    (moneyline/run_line/totals). SIEMPRE se imprime, aunque no haya datos:
    en ese caso muestra un mensaje explícito en vez de omitir la sección.

    `review`: el dict que devuelve tracking.results_tracker.compute_daily_review(),
    o None si ni siquiera se pudo calcular (ver el caller en main.py).
    """
    print("\n" + "=" * 70)
    if review is None or not review.get("review_date"):
        print("  REVISIÓN DEL DÍA ANTERIOR")
        print("=" * 70 + "\n")
        print("Sin datos de ayer para revisar (sin juegos, feriado, o el tracking "
              "todavía no tiene resultado real para esas predicciones).\n")
        return

    print(f"  REVISIÓN DEL {review['review_date']}")
    print("=" * 70 + "\n")

    if review["n_games"] == 0:
        print("Sin datos de ayer para revisar (sin juegos, feriado, o el tracking "
              "todavía no tiene resultado real para esas predicciones).\n")
        return

    for g in review["games"]:
        if g["actual_margin"] > 0:
            winner = g["home_team"]
        elif g["actual_margin"] < 0:
            winner = g["away_team"]
        else:
            winner = "empate"
        print(f"{g['away_team']} @ {g['home_team']}  -- final {g['away_score']}-{g['home_score']} (gana {winner})")

        _print_market_review_line(_MARKET_REVIEW_LABELS["moneyline"], g, "moneyline")
        _print_market_review_line(_MARKET_REVIEW_LABELS["run_line"], g, "run_line")
        _print_market_review_line(_MARKET_REVIEW_LABELS["totals"], g, "totals")
        print("-" * 70)

    _print_daily_review_summary(review)


def print_report(rows: list[dict], picks_by_game: dict | None = None,
                  discarded_games: list[dict] | None = None) -> None:
    if not rows and not discarded_games:
        print("No hay juegos analizados hoy.")
        return

    picks_by_game = picks_by_game or {}

    print("\n" + "=" * 70)
    print(f"  PREDICCIONES DE HOY — {date.today().strftime('%Y-%m-%d')}")
    print("=" * 70 + "\n")

    # Visible en el reporte, no solo en el log (main.py::analyze_today() ya
    # loggea cada descarte a WARNING con el detalle real) -- un juego en
    # curso, pospuesto o de doble cartelera que no se procesó debe quedar
    # claro aquí, junto al conteo de procesados/descartados, en vez de
    # obligar a quien lee el reporte a adivinarlo o ir a buscar el log.
    if discarded_games:
        n = len(discarded_games)
        if n == 1:
            print(f"⏱️ 1 juego no procesado: {discarded_games[0]['message']}")
        else:
            print(f"⏱️ {n} juegos no procesados:")
            for d in discarded_games:
                print(f"  - {d['message']}")
        print()

    if not rows:
        print("No hay juegos analizados hoy.")
        return

    for r in rows:
        print(f"{r['away_team']} @ {r['home_team']}")
        print(f"  Pitchers: {r['away_pitcher'] or 'TBD'}  vs  {r['home_pitcher'] or 'TBD'}")
        if r.get("away_bullpen_era") is not None:
            print(f"  Bullpen  -> visitante: {r['away_bullpen_era']:.2f}   local: {r['home_bullpen_era']:.2f}")
        if r.get("away_k_pct") is not None:
            print(f"  K%       -> visitante: {r['away_k_pct']:.1%}   local: {r['home_k_pct']:.1%}")
        if r.get("away_days_rest") is not None:
            print(f"  Descanso -> visitante: {r['away_days_rest']}d   local: {r['home_days_rest']}d")
        if r.get("park_name"):
            temp_txt = f", {r['temp_f']:.0f}°F" if r.get("temp_f") is not None else ""
            print(f"  Parque   -> {r['park_name']} (factor {r['park_factor']:.2f}{temp_txt})")
        print(f"  Modelo   -> visitante: {r['away_model_prob']:.3f}   local: {r['home_model_prob']:.3f}")

        if r.get("away_skellam_prob") is not None:
            print(f"  Skellam  -> visitante: {r['away_skellam_prob']:.3f}   local: {r['home_skellam_prob']:.3f}"
                  f"   (carreras proy.: {r['away_proj_runs']:.1f} - {r['home_proj_runs']:.1f})")

            # Skellam y NB2 comparten el mismo mu proyectado -- son un solo
            # voto ("familia mu"), no dos independientes. El acuerdo real que
            # importa es heurístico vs. esa familia, no heurístico vs. Skellam
            # vs. NB2 por separado.
            heuristic_favors_away = r["away_model_prob"] > 0.5
            mu_family_favors_away = r["away_skellam_prob"] > 0.5
            agree = ("✅ heurístico y familia Skellam/NB2 coinciden en el favorito"
                     if heuristic_favors_away == mu_family_favors_away
                     else "⚠️  heurístico DISCREPA de la familia Skellam/NB2")
            print(f"  {agree}")

        if r.get("away_negbin_prob") is not None:
            print(f"  Bin.Neg. -> visitante: {r['away_negbin_prob']:.3f}   local: {r['home_negbin_prob']:.3f}"
                  f"   (dispersión k, cola gorda vs. Skellam/Poisson)")

            if r.get("away_skellam_prob") is not None:
                skellam_favors_away = r["away_skellam_prob"] > 0.5
                negbin_favors_away = r["away_negbin_prob"] > 0.5
                if skellam_favors_away != negbin_favors_away:
                    print("  🔀 Skellam y NB2 discrepan entre sí (prácticamente inalcanzable con "
                          "mu/k reales -- casi seguro el artefacto de punto flotante del empate "
                          "exacto, no una señal real de modelo)")

        if r.get("home_covers_rl_prob") is not None:
            print(f"  Run Line -> {r['home_team']} -1.5: {r['home_covers_rl_prob']:.1%}   "
                  f"{r['away_team']} +1.5: {r['away_covers_rl_prob']:.1%}")
        if r.get("fair_total_runs") is not None:
            print(f"  Total    -> línea justa del modelo: {r['fair_total_runs']:.1f} carreras "
                  f"(compárala contra la línea real de tu casa de apuestas)")
        if r.get("away_market_prob") is not None:
            print(f"  Mercado  -> visitante: {r['away_market_prob']:.3f}   local: {r['home_market_prob']:.3f}"
                  f"  (implícita, con vig)")
            if r.get("away_market_no_vig_prob") is not None:
                print(f"  Sin vig  -> visitante: {r['away_market_no_vig_prob']:.3f}   "
                      f"local: {r['home_market_no_vig_prob']:.3f}  (consenso, sin margen de casa)")

            if r.get("market_favorite_team"):
                print(f"  Favorito del mercado -> {r['market_favorite_team']} ({r['market_favorite_prob']:.1%})")
            elif r.get("market_favorite_prob") is not None:
                print(f"  Favorito del mercado -> pick'em, sin favorito claro (~{r['market_favorite_prob']:.1%})")

            print(f"  Edge     -> visitante: {r['away_edge']:+.3f}   local: {r['home_edge']:+.3f}")
            print(f"  EV       -> visitante: {r['away_ev']:+.3f}   local: {r['home_ev']:+.3f}  (por unidad apostada)")

            if r.get("flag_review"):
                print("  🔎 candidato a revisión: edge >= umbral y heurístico coincide con la familia Skellam/NB2 en el favorito")
        else:
            print("  Mercado  -> (sin cuotas cargadas todavía)")

        picks = picks_by_game.get(r["game_pk"], [])
        if picks:
            print("  Picks recomendados:")
            for p in picks:
                tag = "  ⚠️ forzado (sin edge real)" if p.get("forced") else ""
                source = p.get("prob_source")
                source_tag = f"  [fuente: {_PROB_SOURCE_LABELS.get(source, source)}]" if source else ""
                discrepancy_tag = "  ⚡ discrepancia direccional vs. heurístico" if p.get("directional_discrepancy") else ""
                print(f"    • {team_label(p, r):<18}  "
                      f"(edge {p['edge']:+.1%}, EV {p['ev']:+.2f}){tag}{source_tag}{discrepancy_tag}")

        print("-" * 70)


def _write_csv(rows: list[dict], path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None

    if not rows:
        return path

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    return path


def export_csv(rows: list[dict], path: str = None) -> str:
    if path is None:
        path = f"reports/reporte_{date.today().strftime('%Y%m%d')}.csv"
    return _write_csv(rows, path)


def export_picks_csv(rows: list[dict], path: str = None) -> str:
    """Una fila por pick (0 a 3 por partido) — no encaja en export_csv,
    que es una fila por partido, así que vive en su propio archivo."""
    if path is None:
        path = f"reports/picks_{date.today().strftime('%Y%m%d')}.csv"
    return _write_csv(rows, path)
