"""
Genera y selecciona los picks recomendados por partido, en hasta 3 mercados
(moneyline, run_line, totals) — máximo un pick por mercado, mínimo 1 por
partido (ver select_picks_for_game).

No hay obligación de que exista un pick de moneyline: si solo hay cuotas
manuales de totales cargadas para un partido, ese es el único candidato
evaluado — "el mejor mercado disponible", no un mercado fijo.
"""

from config import PICK_PROBABILITY_SOURCE
from model.edge import implied_prob, edge as edge_fn, expected_value
from model.markets import run_line_prob, totals_prob

# Qué campos de `prediction` alimentan la probabilidad de moneyline según
# PICK_PROBABILITY_SOURCE -- mismo mapeo (away_field, home_field) que
# tracking/results_tracker._MODEL_FIELDS, para no inventar una segunda
# convención de nombres de modelo en el proyecto.
_PROB_SOURCE_FIELDS = {
    "heuristic": ("away_model_prob", "home_model_prob"),
    "skellam": ("away_skellam_prob", "home_skellam_prob"),
    "negbin": ("away_negbin_prob", "home_negbin_prob"),
}


def _build_candidate(market: str, selection: str, line, model_prob: float,
                      odds: float, market_novig_prob: float | None,
                      min_ev: float, min_edge: float,
                      prob_source: str | None = None,
                      directional_discrepancy: bool | None = None) -> dict:
    market_prob = market_novig_prob if market_novig_prob is not None else implied_prob(odds)
    e = edge_fn(model_prob, market_prob)
    ev = expected_value(model_prob, odds)
    return {
        "market": market,
        "selection": selection,
        "line": line,
        "model_prob": model_prob,
        "market_prob": market_prob,
        "edge": e,
        "ev": ev,
        "odds_used": odds,
        "viable": (ev > min_ev) or (e > min_edge),
        # Trazabilidad: qué modelo de probabilidad generó ESTE candidato, y
        # si ese modelo discrepa en el favorito con el heurístico -- para
        # moneyline con prob_source distinto de "heuristic", nunca None. En
        # run_line/totals el modelo siempre es Skellam/NB2 (nunca hubo una
        # versión heurística de esos mercados), así que se anota la fuente
        # por consistencia pero la discrepancia direccional no aplica.
        "prob_source": prob_source,
        "directional_discrepancy": directional_discrepancy,
    }


def _best_side(market: str, options: list[tuple], min_ev: float, min_edge: float,
               prob_source: str | None = None, directional_discrepancy: bool | None = None) -> dict | None:
    """options: lista de (selection, line, model_prob, odds, market_novig_prob).
    Devuelve el mejor lado (por EV) de ESE mercado, o None si ningún lado
    tiene cuota disponible."""
    built = [
        _build_candidate(market, sel, line, model_prob, odds, novig, min_ev, min_edge,
                          prob_source=prob_source, directional_discrepancy=directional_discrepancy)
        for (sel, line, model_prob, odds, novig) in options
        if odds is not None
    ]
    if not built:
        return None
    return max(built, key=lambda c: c["ev"])


def generate_pick_candidates(prediction: dict, market_lines: dict,
                              min_ev: float = 0.05, min_edge: float = 0.04,
                              prob_source: str = PICK_PROBABILITY_SOURCE) -> list[dict]:
    """
    prediction: el dict que devuelve model.predictor.predict_from_raw_inputs()
    market_lines: dict opcional por mercado —

        {
          "moneyline": {"home_odds":.., "away_odds":.., "home_novig":.., "away_novig":..},
          "run_line":  {"line": 1.5, "home_odds":.., "away_odds":.., "home_novig":.., "away_novig":..},
          "totals":    {"line": 8.5, "over_odds":.., "under_odds":.., "over_novig":.., "under_novig":..},
        }

    prob_source: qué modelo de `prediction` alimenta moneyline
    ("heuristic"/"skellam"/"negbin", ver _PROB_SOURCE_FIELDS). Por default
    lee config.PICK_PROBABILITY_SOURCE -- se puede pasar explícito para
    testear o para recalcular un snapshot histórico con una fuente distinta
    a la que estaba vigente ese día.

    Un mercado ausente (o sin ambas cuotas) simplemente no genera candidato.
    Devuelve como máximo un candidato por mercado presente (0 a 3 en total).
    """
    candidates = []

    ml = market_lines.get("moneyline")
    if ml:
        away_field, home_field = _PROB_SOURCE_FIELDS[prob_source]
        home_prob = prediction[home_field]
        away_prob = prediction[away_field]

        # Discrepancia direccional: ¿el modelo que realmente decide el pick
        # (prob_source) favorece un lado distinto al que favorece el
        # heurístico? Si prob_source YA ES "heuristic" no hay nada que
        # comparar contra sí mismo -- siempre False.
        if prob_source == "heuristic":
            directional_discrepancy = False
        else:
            source_favors_home = home_prob > 0.5
            heuristic_favors_home = prediction["home_model_prob"] > 0.5
            directional_discrepancy = source_favors_home != heuristic_favors_home

        best = _best_side("moneyline", [
            ("home", None, home_prob, ml.get("home_odds"), ml.get("home_novig")),
            ("away", None, away_prob, ml.get("away_odds"), ml.get("away_novig")),
        ], min_ev, min_edge, prob_source=prob_source, directional_discrepancy=directional_discrepancy)
        if best:
            candidates.append(best)

    rl = market_lines.get("run_line")
    if rl:
        line = rl.get("line", 1.5)
        home_cover_prob, away_cover_prob = run_line_prob(
            prediction["home_proj_runs"], prediction["away_proj_runs"], line
        )
        best = _best_side("run_line", [
            ("home", line, home_cover_prob, rl.get("home_odds"), rl.get("home_novig")),
            ("away", line, away_cover_prob, rl.get("away_odds"), rl.get("away_novig")),
        ], min_ev, min_edge, prob_source="skellam", directional_discrepancy=None)
        if best:
            candidates.append(best)

    totals = market_lines.get("totals")
    if totals and totals.get("line") is not None:
        line = totals["line"]
        over_prob, under_prob = totals_prob(
            prediction["home_proj_runs"], prediction["away_proj_runs"], line
        )
        best = _best_side("totals", [
            ("over", line, over_prob, totals.get("over_odds"), totals.get("over_novig")),
            ("under", line, under_prob, totals.get("under_odds"), totals.get("under_novig")),
        ], min_ev, min_edge, prob_source="skellam", directional_discrepancy=None)
        if best:
            candidates.append(best)

    return candidates


def select_picks_for_game(candidates: list[dict], force_at_least_one: bool = True,
                           max_picks: int = 3) -> list[dict]:
    """
    Si hay candidatos viables (edge/EV por encima del umbral), devuelve
    hasta max_picks de ellos (ya vienen máximo 1 por mercado). Si ninguno
    es viable y force_at_least_one=True, devuelve el menos malo (mayor EV,
    aunque sea negativo) marcado forced=True — para que las métricas nunca
    mezclen señal real con relleno de "siempre al menos 1 pick".

    Si no hay NINGÚN candidato (ningún mercado con cuotas cargadas para
    este partido), no hay nada que forzar: devuelve [].
    """
    viable = [c for c in candidates if c["viable"]]
    if viable:
        ranked = sorted(viable, key=lambda c: c["ev"], reverse=True)
        return [{**c, "forced": False} for c in ranked[:max_picks]]

    if force_at_least_one and candidates:
        best = max(candidates, key=lambda c: c["ev"])
        return [{**best, "forced": True}]

    return []
