"""
MLB EDGE ANALYZER
Orquestador: Realiza auditoría del día anterior y proyecciones del día actual.

La versión del modelo NO se declara aquí -- ver config.MODEL_VERSION, la
única fuente real: es parte literal de la clave de idempotencia de
game_analysis/feature_snapshots (UniqueConstraint en db/database.py).
Antes este docstring decía "Versión 0.6.0" mientras config.MODEL_VERSION
decía "0.5.0-reconectado" al mismo tiempo -- dos números de versión
distintos y desincronizados, y solo uno de los dos afectaba de verdad la
idempotencia. Bump la versión SOLO en config.MODEL_VERSION.
"""

import logging
import time
from datetime import date, datetime, timedelta
from logging_config import setup_logging
from data.mlb_api import get_schedule
from data.stats import (
    get_pitcher_era_ip, get_team_ops, get_league_ops, get_bullpen_era,
    get_pitcher_command, get_pitcher_rest,
)
from data.park_factors import get_park_info
from data.weather import preload_weather
from model.runs_projection import LEAGUE_AVG_ERA
from model.predictor import predict_from_raw_inputs
from model.edge import implied_prob, edge, expected_value, market_favorite, no_vig_probs
from model.picks import generate_pick_candidates, select_picks_for_game
from data.odds_api import fetch_moneyline_odds, match_odds_to_game, consensus_no_vig_prob, best_available_price
from db.database import init_db, save_analysis, save_feature_snapshot, save_picks
from reports.generate_report import print_report, export_csv, export_picks_csv, print_yesterday_review
from tracking.results_tracker import (
    update_results, print_performance_report, print_calibration_report, audit_totals, compute_daily_review,
    count_liquidated_picks_with_market_odds,
)
from config import (
    STARTER_WEIGHT, HOME_FIELD_ADVANTAGE, MODEL_VERSION, REVIEW_EDGE_THRESHOLD,
    MIN_PICK_EV, MIN_PICK_EDGE, FORCE_AT_LEAST_ONE_PICK, MAX_PICKS_PER_GAME,
    PARK_FACTOR_WEIGHT, WEATHER_CORRECTION, NEGBIN_DISPERSION, DATABASE_URL,
    MIN_LIQUIDATED_PICKS_FOR_CALIBRATION,
)
from version_info import get_git_commit

logger = logging.getLogger("mlb_edge_analyzer")

# Cuotas de mercado cargadas a mano — se usan solo si no hay match en vivo
# de The Odds API (ODDS_API_KEY sin configurar, o el juego no aparece en la
# respuesta). Llena esto con el game_pk de cada partido (lo ves corriendo
# `python data/mlb_api.py`) y las cuotas moneyline reales.
MARKET_ODDS = {
    # 717468: {"away": -135, "home": +115},
}

# Run Line y Totales arrancan solo con cuotas manuales — The Odds API cobra
# presupuesto por mercado × región, así que pedir spreads/totals en vivo
# triplicaría el consumo de ODDS_API_MONTHLY_BUDGET por llamada. Se conecta
# en vivo más adelante si el presupuesto lo permite.
# "favorite_side" ("home" o "away") indica quién da -line según el
# mercado real -- si se omite, se asume "home" y se loggea un WARNING (ver
# C1: antes se asumía "home" siempre, sin poder expresar un visitante
# favorito, ~45% de los juegos reales).
MARKET_SPREADS = {
    # 717468: {"line": 1.5, "favorite_side": "home", "home": -120, "away": +100},
}
MARKET_TOTALS = {
    # 717468: {"line": 8.5, "over": -110, "under": -110},
}


def _discard_reason_phrase(detailed_state: str | None) -> str:
    """Frase legible según el estado detallado (detailedState) que trae MLB
    Stats API -- distingue "ya en curso" de "pospuesto"/"suspendido"/
    "retrasado" en vez de agruparlos todos bajo un "descartado" genérico:
    un juego en curso y uno pospuesto son situaciones muy distintas para
    quien lee el reporte (ver CAMBIO 1 de la auditoría de visibilidad de
    descartes)."""
    s = (detailed_state or "").lower()
    if s == "final":
        return "ya terminó (Final) antes de correr el pipeline"
    if "in progress" in s or s == "live":
        return "ya estaba en curso"
    if "postponed" in s:
        return "pospuesto"
    if "suspended" in s:
        return "suspendido"
    if "delayed" in s:
        return "con inicio retrasado"
    return f"estado inusual ({detailed_state})" if detailed_state else "estado desconocido"


def _format_game_time(iso_str: str | None) -> str | None:
    """HH:MM UTC legible a partir del gameDate ISO de MLB Stats API, o None
    si no viene o no se puede parsear -- el mensaje de descarte simplemente
    omite la hora en ese caso, no la inventa."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.strftime("%H:%M UTC")


def _build_discard_message(game: dict, other_games_same_matchup: list[dict]) -> str:
    """Mensaje legible de por qué se descartó `game` por estado distinto de
    "Preview" (incluye "Final" -- ver el comentario en analyze_today() sobre
    por qué un juego ya terminado también se descarta, no solo uno en
    curso/pospuesto/suspendido). Si `other_games_same_matchup` no está vacío (mismo
    away_team_id/home_team_id -- MLB Stats API le da un game_pk DISTINTO a
    cada juego de una doble cartelera, así que la única forma de
    detectarlos es por matchup + gameNumber, nunca por game_pk), identifica
    ambos juegos por separado en vez de reportar un descarte genérico de 1
    juego (ver CAMBIO 3: esto es justo lo que confundió el reporte de
    Phillies @ Royals del 2026-07-06)."""
    reason = _discard_reason_phrase(game.get("status"))
    game_time = _format_game_time(game.get("game_time"))
    time_note = f", inicio programado {game_time}" if game_time else ""
    base = f"{game['away_team']} @ {game['home_team']}"

    if not other_games_same_matchup:
        return f"{base} -- {reason} (estado: {game.get('status')}){time_note}, no se generó predicción."

    sibling = other_games_same_matchup[0]
    own_num = game.get("game_number") or "?"
    sibling_num = sibling.get("game_number") or "?"
    sibling_abstract = sibling.get("abstract_state")
    # "Final" ya NO cuenta como procesado (ver CAMBIO de exclusión de
    # juegos terminados en analyze_today()) -- solo "Preview" sí se
    # procesa; cualquier otro estado, incluido Final, cae al mismo mensaje
    # de "tampoco se procesó" que cualquier otro descarte.
    if sibling_abstract == "Preview":
        sibling_desc = f"juego {sibling_num} sigue en Preview y sí se procesó"
    else:
        sibling_reason = _discard_reason_phrase(sibling.get("status"))
        sibling_desc = f"juego {sibling_num} tampoco se procesó ({sibling_reason}, estado: {sibling.get('status')})"

    return (f"{base} -- juego {own_num} de doble cartelera, {reason} "
            f"(estado: {game.get('status')}){time_note}; {sibling_desc}.")


def analyze_today() -> list[dict]:
    league_ops = get_league_ops()
    games = get_schedule(date.today())
    weather_by_team = preload_weather(games, get_park_info)
    odds_events = fetch_moneyline_odds()
    results = []
    discarded_games = []
    n_discarded = 0
    n_errors = 0

    # Agrupado por matchup (no por game_pk -- ver docstring de
    # _build_discard_message) para poder distinguir un descarte por doble
    # cartelera de uno aislado.
    games_by_matchup: dict[tuple, list[dict]] = {}
    for g in games:
        games_by_matchup.setdefault((g["away_team_id"], g["home_team_id"]), []).append(g)

    for g in games:
        # Solo "Preview" genera predicción. "Final" se descarta A PROPÓSITO
        # (antes se aceptaba junto con Preview): get_pitcher_era_ip()/
        # get_team_ops()/get_bullpen_era() (data/stats.py) piden stats
        # acumuladas de TEMPORADA en el momento de la corrida
        # (params={"stats": "season", ...}), sin ningún corte "as of
        # date" -- si el juego ya es Final cuando corre el pipeline, esas
        # stats YA incluyen el resultado de ese mismo juego (MLB Stats API
        # las actualiza casi en vivo). Generar una "predicción" en ese
        # momento no es una proyección ciega: es un cálculo retroactivo
        # contaminado con el propio resultado que se intenta predecir, y
        # ensucia el histórico de compute_metrics()/calibración con una
        # fila que no representa una predicción real hecha antes del juego.
        if g.get("abstract_state") != "Preview":
            siblings = [
                s for s in games_by_matchup[(g["away_team_id"], g["home_team_id"])]
                if s["game_pk"] != g["game_pk"]
            ]
            message = _build_discard_message(g, siblings)
            logger.warning(f"Omitiendo {message}")
            discarded_games.append({"away_team": g["away_team"], "home_team": g["home_team"], "message": message})
            n_discarded += 1
            continue

        if not g.get("away_pitcher_id") or not g.get("home_pitcher_id"):
            message = f"{g['away_team']} @ {g['home_team']} -- abridor probable sin confirmar (TBD), no se generó predicción."
            logger.warning(f"Omitiendo {message}")
            discarded_games.append({"away_team": g["away_team"], "home_team": g["home_team"], "message": message})
            n_discarded += 1
            continue

        away_era_ip = get_pitcher_era_ip(g["away_pitcher_id"])
        home_era_ip = get_pitcher_era_ip(g["home_pitcher_id"])
        away_ops = get_team_ops(g["away_team_id"])
        home_ops = get_team_ops(g["home_team_id"])

        if None in (away_era_ip, home_era_ip, away_ops, home_ops):
            missing = []
            if away_era_ip is None: missing.append("away ERA/IP")
            if home_era_ip is None: missing.append("home ERA/IP")
            if away_ops is None: missing.append("away OPS")
            if home_ops is None: missing.append("home OPS")
            message = (f"{g['away_team']} @ {g['home_team']} -- falta {', '.join(missing)} "
                       f"(falla de API o datos no disponibles), no se generó predicción.")
            logger.warning(f"Omitiendo {message}")
            discarded_games.append({"away_team": g["away_team"], "home_team": g["home_team"], "message": message})
            n_discarded += 1
            continue

        try:
            row = _analyze_one_game(
                g, league_ops, weather_by_team, odds_events,
                away_era_ip, home_era_ip, away_ops, home_ops,
            )
            results.append(row)
        except Exception as e:
            n_errors += 1
            logger.error(
                f"Error inesperado procesando {g['away_team']} @ {g['home_team']} "
                f"(game_pk={g.get('game_pk')}): {e}",
                exc_info=True,
            )
            continue

    stats = {
        "total_games": len(games), "processed": len(results),
        "discarded": n_discarded, "errors": n_errors,
        "discarded_games": discarded_games,
    }
    analyze_today.last_run_stats = stats
    logger.info(
        f"analyze_today(): {stats['processed']} procesado(s), {stats['discarded']} descartado(s), "
        f"{stats['errors']} con error, de {stats['total_games']} juego(s) totales"
    )
    return results


def _mu_family_agrees_internally(away_skellam_prob: float, away_negbin_prob: float) -> bool:
    """
    Skellam y NB2 reciben el mismo home_mu/away_mu (mismo motor
    project_team_runs()) -- son la misma "familia mu", no dos votos
    independientes. Teóricamente detecta cuando Skellam y NB2 discrepan en
    dirección (cuál lado queda arriba de 0.5).

    En la práctica, con k=7.0 (o cualquier k razonable para carreras de
    MLB), esto es prácticamente inalcanzable para mu_home != mu_away --
    verificado con un barrido exhaustivo de 202,500 combinaciones de
    mu_home/mu_away (rango 1.0-10.0, paso 0.02, con away_prob = 1 -
    home_prob calculado igual que model/predictor.py) y forzando k hasta
    0.01 (dispersión absurda, muy por debajo de cualquier valor con
    sentido para un juego real): cero discrepancias de dirección cuando
    los dos mu son distintos.

    El único disparador real conocido es el empate EXACTO (mu_home ==
    mu_away bit a bit) -- y ni siquiera ahí es universal: de 450 valores
    de mu probados en empate exacto, solo ~30% dispararon el artefacto
    (ej. mu=1.0 sí, mu=4.5 no). La causa es un residuo de punto flotante
    de ~2e-16 en la renormalización de scipy.stats.skellam.cdf dentro de
    skellam_win_prob() -- cuya DIRECCIÓN (por encima o por debajo de 0.5)
    depende del valor específico de mu, no de un sesgo consistente. La
    suma truncada de negbin_win_prob() no arrastra ese residuo (da 0.5
    exacto en todo empate), así que cuando el residuo de Skellam cae del
    lado que cruza 0.5 tras el complemento away=1-home, se ve como
    discrepancia; cuando cae del otro lado, no. No es una discrepancia
    real de modelo ni una señal de que la dispersión cambie el favorito
    -- ver tests/test_model_agreement_real.py para la verificación
    completa con las funciones reales (incluye la corrección de una
    primera investigación que comparaba mal el lado "away").

    Se deja la comparación con `>` estricto (sin tolerancia/epsilon) a
    propósito: este flag no alimenta flag_review ni ningún pick/edge/EV
    real (solo un log informativo y una línea del reporte), así que el
    artefacto no tiene ningún costo -- agregar un epsilon introduciría un
    número mágico nuevo a mantener por un caso sin consecuencia real. Este
    flag existe como red de seguridad ante ese caso límite y ante un k
    futuro mucho más extremo del actual, no como detector de un fenómeno
    que ocurra hoy.
    """
    return (away_skellam_prob > 0.5) == (away_negbin_prob > 0.5)


def _heuristic_agrees_with_mu_family(away_model_prob: float, away_skellam_prob: float) -> bool:
    """¿El heurístico (ERA/OPS) favorece el mismo lado que la familia mu
    (Skellam, representante de esa familia ya que casi nunca discrepa
    internamente con NB2)? Los 2 votos reales del sistema, no 3."""
    return (away_model_prob > 0.5) == (away_skellam_prob > 0.5)


def _analyze_one_game(g, league_ops, weather_by_team, odds_events,
                       away_era_ip, home_era_ip, away_ops, home_ops) -> dict:
    """Cuerpo de análisis de un solo juego, extraído de analyze_today() para
    que el try/except de aislamiento de errores por juego (un juego con un
    dato inesperado no debe tumbar el resto del día) tenga un límite claro,
    en vez de envolver un bloque de 150 líneas inline."""
    away_era, away_innings_pitched = away_era_ip
    home_era, home_innings_pitched = home_era_ip

    away_bullpen = get_bullpen_era(g["away_team_id"])
    home_bullpen = get_bullpen_era(g["home_team_id"])
    away_cmd = get_pitcher_command(g["away_pitcher_id"])
    home_cmd = get_pitcher_command(g["home_pitcher_id"])
    away_rest = get_pitcher_rest(g["away_pitcher_id"])
    home_rest = get_pitcher_rest(g["home_pitcher_id"])
    park = get_park_info(g["home_team_id"])
    weather = weather_by_team.get(g["home_team_id"], {"temp_f": None})

    # Cuotas: primero se intenta la mejor disponible en vivo (The Odds
    # API); si no hay match para este juego, cae a MARKET_ODDS manual.
    odds_event = match_odds_to_game(odds_events, g["away_team"], g["home_team"],
                                     game_datetime_iso=g.get("game_time"))
    live_price = best_available_price(odds_event) if odds_event else None
    no_vig = consensus_no_vig_prob(odds_event) if odds_event else None
    price = live_price or MARKET_ODDS.get(g["game_pk"])

    # Run Line y Totales: solo cuotas manuales por ahora (ver
    # MARKET_SPREADS/MARKET_TOTALS arriba) — se calcula su no-vig igual
    # que en moneyline, cuando ambos lados están cargados.
    manual_rl = MARKET_SPREADS.get(g["game_pk"])
    if manual_rl and manual_rl.get("home") is not None and manual_rl.get("away") is not None:
        rl_home_novig, rl_away_novig = no_vig_probs(manual_rl["home"], manual_rl["away"])
    else:
        rl_home_novig = rl_away_novig = None

    manual_totals = MARKET_TOTALS.get(g["game_pk"])
    if manual_totals and manual_totals.get("over") is not None and manual_totals.get("under") is not None:
        totals_over_novig, totals_under_novig = no_vig_probs(manual_totals["over"], manual_totals["under"])
    else:
        totals_over_novig = totals_under_novig = None

    # Insumos crudos, congelados en el Feature Snapshot Store — el
    # mismo dict alimenta model.predictor.predict_from_raw_inputs()
    # hoy en vivo, y a cualquier recálculo histórico futuro que lea
    # este snapshot en vez de volver a golpear ninguna API. Las cuotas
    # de RL/Totales también se congelan aquí: los odds APIs gratuitos
    # no dan cuotas históricas, así que este es el único momento en
    # que se pueden capturar para un backtest futuro de picks.
    raw_inputs = {
        "away_era": away_era, "home_era": home_era,
        "away_innings_pitched": away_innings_pitched, "home_innings_pitched": home_innings_pitched,
        "away_ops": away_ops, "home_ops": home_ops, "league_ops": league_ops,
        "league_era": LEAGUE_AVG_ERA,
        "away_bullpen_era": away_bullpen, "home_bullpen_era": home_bullpen,
        "away_k_pct": away_cmd["k_pct"], "away_bb_pct": away_cmd["bb_pct"],
        "home_k_pct": home_cmd["k_pct"], "home_bb_pct": home_cmd["bb_pct"],
        "away_days_rest": away_rest["days_rest"], "away_last_outing_pitches": away_rest["last_outing_pitches"],
        "home_days_rest": home_rest["days_rest"], "home_last_outing_pitches": home_rest["last_outing_pitches"],
        "park_factor": park["park_factor"], "park_name": park["name"],
        "temp_f": weather.get("temp_f"), "wind_mph": weather.get("wind_mph"),
        "wind_direction_deg": weather.get("wind_direction_deg"),
        "market_price": price, "market_no_vig": no_vig,
        "market_run_line": manual_rl, "market_totals": manual_totals,
        "starter_weight": STARTER_WEIGHT, "home_field_advantage": HOME_FIELD_ADVANTAGE,
        "park_factor_weight": PARK_FACTOR_WEIGHT, "weather_correction": WEATHER_CORRECTION,
        "negbin_dispersion": NEGBIN_DISPERSION,
    }

    prediction = predict_from_raw_inputs(raw_inputs)
    away_mu, home_mu = prediction["away_proj_runs"], prediction["home_proj_runs"]
    away_model_prob, home_model_prob = prediction["away_model_prob"], prediction["home_model_prob"]
    away_skellam_prob, home_skellam_prob = prediction["away_skellam_prob"], prediction["home_skellam_prob"]
    away_negbin_prob, home_negbin_prob = prediction["away_negbin_prob"], prediction["home_negbin_prob"]
    home_covers_rl_prob, away_covers_rl_prob = prediction["home_covers_rl_prob"], prediction["away_covers_rl_prob"]
    fair_total_runs = prediction["fair_total_runs"]

    if price:
        away_market_prob = implied_prob(price["away"])
        home_market_prob = implied_prob(price["home"])
        away_edge = edge(away_model_prob, away_market_prob)
        home_edge = edge(home_model_prob, home_market_prob)
        away_ev = expected_value(away_model_prob, price["away"])
        home_ev = expected_value(home_model_prob, price["home"])
    else:
        away_market_prob = home_market_prob = None
        away_edge = home_edge = None
        away_ev = home_ev = None

    if no_vig:
        away_market_no_vig_prob, home_market_no_vig_prob = no_vig
    else:
        away_market_no_vig_prob = home_market_no_vig_prob = None

    # Favorito del mercado — se calcula sobre el consenso sin vig
    # cuando existe (magnitud honesta); si solo hay una cuota manual
    # (con vig incluido) se usa esa, es lo único disponible.
    fav = None
    if away_market_no_vig_prob is not None:
        fav = market_favorite(g["away_team"], g["home_team"], away_market_no_vig_prob, home_market_no_vig_prob)
    elif away_market_prob is not None:
        fav = market_favorite(g["away_team"], g["home_team"], away_market_prob, home_market_prob)

    model_edge_vs_market_favorite = None
    if fav is not None and fav["side"] is not None and away_edge is not None:
        model_edge_vs_market_favorite = home_edge if fav["side"] == "home" else away_edge

    # mu_family_agrees_internally en False es prácticamente inalcanzable con
    # valores reales de MLB (ver docstring de la función) -- si aparece, casi
    # seguro es el artefacto de punto flotante del empate exacto mu_home==
    # mu_away, no una señal real de que la dispersión cambió el favorito.
    # Se deja el log igual: barato, y sirve de red de seguridad si algún día
    # sí ocurre por una razón real (ej. un k futuro mucho más extremo).
    if not _mu_family_agrees_internally(away_skellam_prob, away_negbin_prob):
        logger.info(f"{g['away_team']} @ {g['home_team']}: Skellam y NB2 discrepan entre sí en el "
                    f"favorito (away: Skellam={away_skellam_prob:.3f} NB2={away_negbin_prob:.3f}) -- "
                    f"probablemente el artefacto de punto flotante del empate exacto, no una señal real")

    # Candidato a revisión: edge por encima del umbral Y los dos VOTOS
    # reales (heurístico vs. familia mu Skellam+NB2) de acuerdo en el
    # favorito -- ver _heuristic_agrees_with_mu_family arriba. Es una
    # preselección para que decidas tú — nunca una apuesta automática.
    heuristic_agrees_with_mu_family = _heuristic_agrees_with_mu_family(away_model_prob, away_skellam_prob)
    flag_review = bool(
        heuristic_agrees_with_mu_family
        and away_edge is not None
        and max(abs(away_edge), abs(home_edge)) >= REVIEW_EDGE_THRESHOLD
    )

    # Picks recomendados — hasta 1 por mercado (moneyline/run_line/
    # totals), como máximo MAX_PICKS_PER_GAME por partido. Un mercado
    # sin cuotas cargadas simplemente no compite (no hay obligación de
    # que el pick de moneyline exista). Si ninguno tiene edge real,
    # FORCE_AT_LEAST_ONE_PICK genera el menos malo marcado forced=True.
    market_lines = {}
    if price:
        market_lines["moneyline"] = {
            "home_odds": price["home"], "away_odds": price["away"],
            "home_novig": home_market_no_vig_prob, "away_novig": away_market_no_vig_prob,
        }
    if manual_rl:
        rl_favorite_side = manual_rl.get("favorite_side")
        if rl_favorite_side is None:
            rl_favorite_side = "home"
            logger.warning(
                f"{g['away_team']} @ {g['home_team']}: MARKET_SPREADS sin favorite_side explícito -- "
                f"asumiendo 'home' (línea -1.5 para el local). Verifica cuál equipo es el favorito real "
                f"del mercado antes de confiar en este pick."
            )
        market_lines["run_line"] = {
            "line": manual_rl.get("line", 1.5),
            "favorite_side": rl_favorite_side,
            "home_odds": manual_rl.get("home"), "away_odds": manual_rl.get("away"),
            "home_novig": rl_home_novig, "away_novig": rl_away_novig,
        }
    if manual_totals:
        market_lines["totals"] = {
            "line": manual_totals.get("line"),
            "over_odds": manual_totals.get("over"), "under_odds": manual_totals.get("under"),
            "over_novig": totals_over_novig, "under_novig": totals_under_novig,
        }

    if not market_lines:
        logger.warning(f"{g['away_team']} @ {g['home_team']}: sin datos de mercado en ningún mercado "
                       f"(moneyline/run_line/totals) -- no se podrá generar ningún pick real para este juego")

    candidates = generate_pick_candidates(prediction, market_lines,
                                           min_ev=MIN_PICK_EV, min_edge=MIN_PICK_EDGE)
    picks = select_picks_for_game(candidates, force_at_least_one=FORCE_AT_LEAST_ONE_PICK,
                                   max_picks=MAX_PICKS_PER_GAME)

    # Fecha oficial del juego según la MLB Stats API (officialDate, en
    # hora ET), no la fecha local de la máquina que corre el pipeline.
    # Un juego que empieza cerca de medianoche UTC puede caer en un
    # "día" distinto según de dónde se corra main.py -- game_date debe
    # reflejar el día del juego, no el día de quien lo ejecuta.
    game_date = g.get("game_date_official") or date.today().strftime("%Y-%m-%d")

    row = {
        "game_pk": g["game_pk"],
        "game_date": game_date,
        "away_team": g["away_team"],
        "home_team": g["home_team"],
        "away_pitcher": g.get("away_pitcher_name"),
        "home_pitcher": g.get("home_pitcher_name"),
        "away_bullpen_era": round(away_bullpen, 2),
        "home_bullpen_era": round(home_bullpen, 2),
        "away_k_pct": away_cmd["k_pct"],
        "home_k_pct": home_cmd["k_pct"],
        "away_days_rest": away_rest["days_rest"],
        "home_days_rest": home_rest["days_rest"],
        "park_name": park["name"],
        "park_factor": park["park_factor"],
        "temp_f": weather.get("temp_f"),
        "away_proj_runs": round(away_mu, 2),
        "home_proj_runs": round(home_mu, 2),
        "away_model_prob": away_model_prob,
        "home_model_prob": home_model_prob,
        "away_skellam_prob": away_skellam_prob,
        "home_skellam_prob": home_skellam_prob,
        "away_negbin_prob": away_negbin_prob,
        "home_negbin_prob": home_negbin_prob,
        "home_covers_rl_prob": home_covers_rl_prob,
        "away_covers_rl_prob": away_covers_rl_prob,
        "fair_total_runs": round(fair_total_runs, 2),
        "away_market_prob": away_market_prob,
        "home_market_prob": home_market_prob,
        "away_market_no_vig_prob": away_market_no_vig_prob,
        "home_market_no_vig_prob": home_market_no_vig_prob,
        "market_favorite_team": fav["team"] if fav else None,
        "market_favorite_side": fav["side"] if fav else None,
        "market_favorite_prob": fav["prob"] if fav else None,
        "model_edge_vs_market_favorite": model_edge_vs_market_favorite,
        "away_edge": away_edge,
        "home_edge": home_edge,
        "away_ev": away_ev,
        "home_ev": home_ev,
        "flag_review": flag_review,
        "model_version": MODEL_VERSION,
        "git_commit": get_git_commit(),
        # Claves internas — run_pipeline() las extrae antes de guardar
        # en GameAnalysis (no son columnas de esa tabla): _feature_snapshot
        # es el punto-en-el-tiempo para recálculos futuros vía
        # model.predictor; _picks son los picks recomendados, que se
        # persisten en su propia tabla (Pick) vía save_picks().
        "_feature_snapshot": raw_inputs,
        "_picks": picks,
    }

    return row



def _sqlite_persistence_risk_warning(database_url: str) -> str | None:
    """Si `database_url` es SQLite (sin DATABASE_URL externo configurado),
    devuelve el aviso de riesgo de persistencia -- None si no aplica.

    En GitHub Actions, un mlb_edge.db SQLite solo sobrevive entre corridas
    vía actions/cache (ver .github/workflows/daily_pipeline.yml), que el
    propio workflow documenta como "best-effort": GitHub libera las cachés
    no tocadas en 7+ días, o si se excede el límite de 10GB del repo. Si
    eso pasa, el histórico completo (game_analysis/picks/feature_snapshots)
    desaparece EN SILENCIO -- el pipeline simplemente arranca de cero sin
    ningún error, y nada en el log lo distingue de un primer uso legítimo.
    Este aviso no soluciona el riesgo (eso requiere un Postgres real
    externo, ej. Neon/Supabase gratuito, vía el secret DATABASE_URL -- una
    cuenta que solo el dueño del proyecto puede crear), pero lo saca de la
    invisibilidad: aparece en CADA corrida mientras no haya un
    DATABASE_URL externo, en vez de quedar como un riesgo documentado solo
    en un comentario que nadie más lee."""
    if not database_url.startswith("sqlite"):
        return None
    return (
        "Corriendo sobre SQLite local sin DATABASE_URL externo configurado -- la persistencia "
        "del histórico entre corridas depende de actions/cache (best-effort: GitHub puede "
        "liberarla en silencio a los 7+ días de inactividad, o si se excede el límite de 10GB "
        "del repo). Para persistencia real, configura el secret DATABASE_URL con un Postgres "
        "gratuito (ej. Neon, Supabase) -- ver .github/workflows/daily_pipeline.yml."
    )


def _calibration_phase_note(n_liquidated_picks: int, min_liquidated_picks: int) -> str | None:
    """Nota de fase de calibración si el histórico de EDGES REALMENTE
    PROBADOS (n_liquidated_picks, ver
    tracking.results_tracker.count_liquidated_picks_with_market_odds())
    todavía no alcanza min_liquidated_picks
    (config.MIN_LIQUIDATED_PICKS_FOR_CALIBRATION) -- None si ya se alcanzó
    el umbral (los picks de hoy sí cuentan como señal real, no solo
    recolección de datos). Cuenta PICKS liquidados con cuota de mercado
    real, no juegos con resultado final -- esa es la pregunta que ya
    responde print_calibration_report() (calibración de la probabilidad
    cruda del modelo, no del edge). Ver Pick.calibration_phase en
    db/database.py: el mismo booleano que esta nota resume se guarda por
    pick, para poder excluirlos de un análisis de ROI futuro."""
    if n_liquidated_picks >= min_liquidated_picks:
        return None
    return (
        f"🧪 Fase de calibración: {n_liquidated_picks}/{min_liquidated_picks} picks liquidados con cuota "
        f"de mercado real -- con esta muestra, los edges/EV del heurístico probablemente reflejan error "
        f"del modelo sin calibrar, no ineficiencia real de mercado. Los picks de hoy se generan y "
        f"guardan igual (para acumular historial), pero no deberían tratarse como señal apostable todavía."
    )


def run_pipeline():
    start_time = time.monotonic()
    setup_logging()
    init_db()

    persistence_warning = _sqlite_persistence_risk_warning(DATABASE_URL)
    if persistence_warning:
        logger.warning(persistence_warning)

    git_commit = get_git_commit()
    logger.info(f"Iniciando run_pipeline() -- model_version={MODEL_VERSION} git_commit={git_commit}")

    # 1. Auditoría de resultados y desempeño del día(s) anterior(es) --
    # ventana rodante de 30/90 días (print_performance_report/
    # print_calibration_report/audit_totals), complementaria a la Sección 1
    # del reporte diario de abajo (que es exactamente UN día, para revisar
    # ayer partido por partido).
    #
    # print_calibration_report() vivía SOLO en track_results.py, un script
    # separado que nunca corre en el cron de producción (daily_pipeline.yml
    # solo ejecuta `python main.py`) -- la calibración por bucket de
    # confianza jamás aparecía en un reporte real por eso. track_results.py
    # además duplicaría la llamada a update_results() de la línea de abajo
    # sin ningún beneficio (ya no quedarían resultados pendientes que
    # actualizar), así que la solución es traer la llamada aquí, no agregar
    # un segundo script al workflow.
    print("\n--- 🔍 AUDITANDO RESULTADOS DEL DÍA ANTERIOR ---")
    updated = update_results()
    print(f"Resultados actualizados: {updated}")
    print_performance_report()
    print_calibration_report()
    audit_totals()

    # Sección 1 del reporte diario: revisión de ayer, mercado por mercado.
    # SIEMPRE se imprime (aunque no haya datos) y SIEMPRE antes de la
    # Sección 2 (predicciones de hoy) -- print_yesterday_review() maneja
    # el caso "sin datos" explícitamente, nunca se omite en silencio.
    yesterday_str = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_review = compute_daily_review(yesterday_str)
    print_yesterday_review(yesterday_review)

    # 2. Análisis
    print("\n--- ⚾ GENERANDO PREDICCIONES PARA HOY ---")
    results = analyze_today()
    # analyze_today() deja sus propias cifras en un atributo de función --
    # evita tener que recorrer `results` de nuevo o repetir la llamada a la
    # API solo para saber cuántos juegos se descartaron o fallaron con error.
    stats = getattr(analyze_today, "last_run_stats", None) or {
        "total_games": len(results), "processed": len(results), "discarded": 0, "errors": 0,
        "discarded_games": [],
    }

    n_liquidated_picks = count_liquidated_picks_with_market_odds()
    calibration_note = _calibration_phase_note(n_liquidated_picks, MIN_LIQUIDATED_PICKS_FOR_CALIBRATION)
    in_calibration_phase = calibration_note is not None

    picks_by_game = {}
    all_picks_rows = []

    for r in results:
        snapshot = r.pop("_feature_snapshot", None)
        picks = r.pop("_picks", [])

        save_analysis(r)
        if snapshot is not None:
            save_feature_snapshot(r["game_pk"], r["game_date"], snapshot)
        if picks:
            for p in picks:
                p["calibration_phase"] = in_calibration_phase
            save_picks(r["game_pk"], r["game_date"], picks, MODEL_VERSION)
            picks_by_game[r["game_pk"]] = picks
            for p in picks:
                all_picks_rows.append({
                    "game_pk": r["game_pk"], "game_date": r["game_date"],
                    "away_team": r["away_team"], "home_team": r["home_team"],
                    **p,
                })

    # print_report() se llama SIEMPRE, incluso sin resultados -- así el
    # detalle de juegos descartados (ver analyze_today()) queda visible en
    # el reporte aunque todos los juegos del día se hayan descartado, en
    # vez de perderse detrás de un "no hay juegos" genérico.
    print_report(results, picks_by_game=picks_by_game, discarded_games=stats.get("discarded_games"),
                 calibration_note=calibration_note)

    if results:
        path = export_csv(results)
        print(f"\nReporte de hoy exportado a: {path}")

        if all_picks_rows:
            picks_path = export_picks_csv(all_picks_rows)
            print(f"Picks exportados a: {picks_path}")

    elapsed = time.monotonic() - start_time

    print(f"\n{'=' * 60}")
    print("  RESUMEN FINAL")
    print(f"{'=' * 60}")
    print(f"Versión del modelo:   {MODEL_VERSION}")
    print(f"Commit utilizado:     {git_commit}")
    print(f"Juegos totales:       {stats['total_games']}")
    print(f"Juegos procesados:    {stats['processed']}")
    print(f"Juegos descartados:   {stats['discarded']}")
    print(f"Errores encontrados:  {stats['errors']}")
    print(f"Tiempo total:         {elapsed:.1f}s")
    print(f"{'=' * 60}\n")

    logger.info(
        f"run_pipeline() completo en {elapsed:.1f}s -- model_version={MODEL_VERSION} "
        f"git_commit={git_commit} total={stats['total_games']} procesados={stats['processed']} "
        f"descartados={stats['discarded']} errores={stats['errors']}"
    )


if __name__ == "__main__":
    run_pipeline()
