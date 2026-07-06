"""
Pruebas de model/picks.py — generación y selección de picks recomendados,
sin tocar red ni base de datos.
"""

from config import NEGBIN_DISPERSION
from model.picks import generate_pick_candidates, select_picks_for_game
from model.markets import run_line_prob, totals_prob
from model.negbin_model import negbin_run_line_prob, negbin_totals_prob, negbin_win_prob
from model.skellam_model import skellam_win_prob


def _prediction(**overrides) -> dict:
    base = {
        "home_model_prob": 0.40, "away_model_prob": 0.60,
        "home_proj_runs": 3.5, "away_proj_runs": 5.0,
    }
    base.update(overrides)
    # model/picks.py lee, por default, PICK_PROBABILITY_SOURCE="skellam"
    # para moneyline -- estas pruebas ejercitan la MECÁNICA de generación/
    # selección de candidatos, no qué modelo en particular alimenta la
    # probabilidad, así que el fixture espeja Skellam/NB2 al heurístico
    # salvo que el test override explícitamente alguno de los dos.
    base.setdefault("home_skellam_prob", base["home_model_prob"])
    base.setdefault("away_skellam_prob", base["away_model_prob"])
    base.setdefault("home_negbin_prob", base["home_model_prob"])
    base.setdefault("away_negbin_prob", base["away_model_prob"])
    return base


def test_generate_pick_candidates_skips_markets_without_data():
    # Solo hay cuotas de moneyline -- "no ML obligatorio" también implica
    # lo inverso: no se inventan candidatos de RL/Totales sin cuotas.
    candidates = generate_pick_candidates(_prediction(), {
        "moneyline": {"home_odds": 130, "away_odds": -150, "home_novig": 0.40, "away_novig": 0.60},
    })
    assert len(candidates) == 1
    assert candidates[0]["market"] == "moneyline"


def test_generate_pick_candidates_only_totals_available():
    # El mercado "obligatorio" no existe: si solo hay totales cargados, ese
    # es el único candidato -- no se fuerza un candidato de moneyline vacío.
    candidates = generate_pick_candidates(_prediction(), {
        "totals": {"line": 8.5, "over_odds": -110, "under_odds": -110, "over_novig": None, "under_novig": None},
    })
    assert len(candidates) == 1
    assert candidates[0]["market"] == "totals"


def test_generate_pick_candidates_evaluates_up_to_three_markets():
    market_lines = {
        "moneyline": {"home_odds": 130, "away_odds": -150, "home_novig": 0.40, "away_novig": 0.60},
        "run_line": {"line": 1.5, "home_odds": 150, "away_odds": -180, "home_novig": None, "away_novig": None},
        "totals": {"line": 8.5, "over_odds": -110, "under_odds": -110, "over_novig": None, "under_novig": None},
    }
    candidates = generate_pick_candidates(_prediction(), market_lines)
    assert {c["market"] for c in candidates} == {"moneyline", "run_line", "totals"}
    assert len(candidates) == 3


def test_generate_pick_candidates_picks_best_side_per_market_not_both():
    # Debe haber COMO MUCHO un candidato de moneyline (el mejor lado), no dos.
    candidates = generate_pick_candidates(_prediction(), {
        "moneyline": {"home_odds": 130, "away_odds": -150, "home_novig": 0.40, "away_novig": 0.60},
    })
    ml_candidates = [c for c in candidates if c["market"] == "moneyline"]
    assert len(ml_candidates) == 1


def test_select_picks_for_game_returns_all_viable_up_to_max():
    # Ambos mercados con edge claro a favor del modelo -> deben quedar viables.
    candidates = generate_pick_candidates(_prediction(), {
        "moneyline": {"home_odds": 200, "away_odds": -250, "home_novig": 0.35, "away_novig": 0.65},
        "totals": {"line": 8.5, "over_odds": 150, "under_odds": -180, "over_novig": None, "under_novig": None},
    }, min_ev=0.05, min_edge=0.04)

    picks = select_picks_for_game(candidates, force_at_least_one=True, max_picks=3)

    assert all(not p["forced"] for p in picks)
    assert len(picks) >= 1


def test_select_picks_for_game_forces_least_bad_when_nothing_viable():
    # Cuotas cercanas a la probabilidad del modelo -> sin edge real.
    candidates = generate_pick_candidates(_prediction(home_model_prob=0.50, away_model_prob=0.50), {
        "moneyline": {"home_odds": 100, "away_odds": -100, "home_novig": 0.50, "away_novig": 0.50},
    }, min_ev=0.05, min_edge=0.04)

    picks = select_picks_for_game(candidates, force_at_least_one=True, max_picks=3)

    assert len(picks) == 1
    assert picks[0]["forced"] is True


def test_select_picks_for_game_returns_empty_without_force_and_without_edge():
    candidates = generate_pick_candidates(_prediction(home_model_prob=0.50, away_model_prob=0.50), {
        "moneyline": {"home_odds": 100, "away_odds": -100, "home_novig": 0.50, "away_novig": 0.50},
    }, min_ev=0.05, min_edge=0.04)

    picks = select_picks_for_game(candidates, force_at_least_one=False, max_picks=3)

    assert picks == []


def test_select_picks_for_game_returns_empty_when_no_candidates_at_all():
    # Ningún mercado con cuotas cargadas -- no hay nada que forzar.
    picks = select_picks_for_game([], force_at_least_one=True, max_picks=3)
    assert picks == []


def test_select_picks_for_game_respects_max_picks():
    market_lines = {
        "moneyline": {"home_odds": 200, "away_odds": -250, "home_novig": 0.35, "away_novig": 0.65},
        "run_line": {"line": 1.5, "home_odds": 200, "away_odds": -250, "home_novig": None, "away_novig": None},
        "totals": {"line": 8.5, "over_odds": 200, "under_odds": -250, "over_novig": None, "under_novig": None},
    }
    candidates = generate_pick_candidates(_prediction(), market_lines, min_ev=0.01, min_edge=0.01)
    picks = select_picks_for_game(candidates, force_at_least_one=True, max_picks=2)
    assert len(picks) <= 2


# --- PICK_PROBABILITY_SOURCE: qué modelo alimenta moneyline ---

def test_moneyline_defaults_to_skellam_probability_source():
    # home_model_prob (heurístico) y home_skellam_prob DIFERENTES a
    # propósito -- si el candidato usa el valor de Skellam (0.70), no el
    # heurístico (0.40), confirma que el default realmente cambió de fuente.
    prediction = _prediction(home_model_prob=0.40, away_model_prob=0.60,
                              home_skellam_prob=0.70, away_skellam_prob=0.30)
    candidates = generate_pick_candidates(prediction, {
        "moneyline": {"home_odds": 130, "away_odds": -150, "home_novig": 0.40, "away_novig": 0.60},
    })
    ml = next(c for c in candidates if c["market"] == "moneyline")
    assert ml["prob_source"] == "skellam"
    assert ml["model_prob"] in (0.70, 0.30)  # el lado elegido usa el prob de Skellam, no el heurístico


def test_moneyline_can_use_heuristic_source_explicitly():
    prediction = _prediction(home_model_prob=0.40, away_model_prob=0.60,
                              home_skellam_prob=0.70, away_skellam_prob=0.30)
    candidates = generate_pick_candidates(prediction, {
        "moneyline": {"home_odds": 130, "away_odds": -150, "home_novig": 0.40, "away_novig": 0.60},
    }, prob_source="heuristic")
    ml = next(c for c in candidates if c["market"] == "moneyline")
    assert ml["prob_source"] == "heuristic"
    assert ml["model_prob"] in (0.40, 0.60)


def test_moneyline_can_use_negbin_source_explicitly():
    prediction = _prediction(home_model_prob=0.40, away_model_prob=0.60,
                              home_negbin_prob=0.55, away_negbin_prob=0.45)
    candidates = generate_pick_candidates(prediction, {
        "moneyline": {"home_odds": 130, "away_odds": -150, "home_novig": 0.40, "away_novig": 0.60},
    }, prob_source="negbin")
    ml = next(c for c in candidates if c["market"] == "moneyline")
    assert ml["prob_source"] == "negbin"
    assert ml["model_prob"] in (0.55, 0.45)


def test_directional_discrepancy_true_when_skellam_and_heuristic_disagree():
    # Heurístico favorece al local (0.55 > 0.5); Skellam favorece al
    # visitante (home 0.45 < 0.5) -- discrepancia direccional real.
    prediction = _prediction(home_model_prob=0.55, away_model_prob=0.45,
                              home_skellam_prob=0.45, away_skellam_prob=0.55)
    candidates = generate_pick_candidates(prediction, {
        "moneyline": {"home_odds": 130, "away_odds": -150, "home_novig": 0.45, "away_novig": 0.55},
    })
    ml = next(c for c in candidates if c["market"] == "moneyline")
    assert ml["directional_discrepancy"] is True


def test_directional_discrepancy_false_when_skellam_and_heuristic_agree():
    prediction = _prediction(home_model_prob=0.45, away_model_prob=0.55,
                              home_skellam_prob=0.40, away_skellam_prob=0.60)
    candidates = generate_pick_candidates(prediction, {
        "moneyline": {"home_odds": 130, "away_odds": -150, "home_novig": 0.40, "away_novig": 0.60},
    })
    ml = next(c for c in candidates if c["market"] == "moneyline")
    assert ml["directional_discrepancy"] is False


def test_directional_discrepancy_always_false_with_heuristic_source():
    # prob_source="heuristic" no tiene nada distinto contra qué comparar --
    # discrepancia siempre False, nunca None ni True.
    prediction = _prediction(home_model_prob=0.55, away_model_prob=0.45,
                              home_skellam_prob=0.45, away_skellam_prob=0.55)
    candidates = generate_pick_candidates(prediction, {
        "moneyline": {"home_odds": 130, "away_odds": -150, "home_novig": 0.45, "away_novig": 0.55},
    }, prob_source="heuristic")
    ml = next(c for c in candidates if c["market"] == "moneyline")
    assert ml["directional_discrepancy"] is False


def test_run_line_and_totals_always_report_skellam_source_no_discrepancy():
    # run_line/totals nunca tuvieron una versión heurística -- siempre
    # Skellam, y la discrepancia direccional no aplica (None, no False).
    market_lines = {
        "run_line": {"line": 1.5, "home_odds": 150, "away_odds": -180, "home_novig": None, "away_novig": None},
        "totals": {"line": 8.5, "over_odds": -110, "under_odds": -110, "over_novig": None, "under_novig": None},
    }
    candidates = generate_pick_candidates(_prediction(), market_lines, prob_source="heuristic")
    for c in candidates:
        assert c["prob_source"] == "skellam"
        assert c["directional_discrepancy"] is None


# --- NB2 conectado de verdad a Run Line y Totales ---

def test_run_line_uses_negbin_probabilities_when_prob_source_is_negbin():
    prediction = _prediction(home_proj_runs=3.5, away_proj_runs=5.0)
    market_lines = {
        "run_line": {"line": 1.5, "home_odds": 150, "away_odds": -180, "home_novig": None, "away_novig": None},
    }
    candidates = generate_pick_candidates(prediction, market_lines, prob_source="negbin")
    rl = next(c for c in candidates if c["market"] == "run_line")

    home_cover_negbin, away_cover_negbin = negbin_run_line_prob(3.5, 5.0, NEGBIN_DISPERSION, 1.5)
    expected_prob = home_cover_negbin if rl["selection"] == "home" else away_cover_negbin

    assert rl["prob_source"] == "negbin"
    assert abs(rl["model_prob"] - expected_prob) < 1e-9
    # Confirma que de verdad es una probabilidad DISTINTA a la de Skellam
    # (si no, la prueba no protegería nada -- podría estar llamando a la
    # función equivocada y pasar por coincidencia).
    home_cover_skellam, away_cover_skellam = run_line_prob(3.5, 5.0, 1.5)
    expected_skellam = home_cover_skellam if rl["selection"] == "home" else away_cover_skellam
    assert abs(expected_prob - expected_skellam) > 1e-6


def test_run_line_uses_skellam_probabilities_when_prob_source_is_skellam():
    prediction = _prediction(home_proj_runs=3.5, away_proj_runs=5.0)
    market_lines = {
        "run_line": {"line": 1.5, "home_odds": 150, "away_odds": -180, "home_novig": None, "away_novig": None},
    }
    candidates = generate_pick_candidates(prediction, market_lines, prob_source="skellam")
    rl = next(c for c in candidates if c["market"] == "run_line")

    home_cover_skellam, away_cover_skellam = run_line_prob(3.5, 5.0, 1.5)
    expected_prob = home_cover_skellam if rl["selection"] == "home" else away_cover_skellam

    assert rl["prob_source"] == "skellam"
    assert abs(rl["model_prob"] - expected_prob) < 1e-9


def test_totals_uses_negbin_probabilities_when_prob_source_is_negbin():
    prediction = _prediction(home_proj_runs=3.5, away_proj_runs=5.0)
    market_lines = {
        "totals": {"line": 8.5, "over_odds": -110, "under_odds": -110, "over_novig": None, "under_novig": None},
    }
    candidates = generate_pick_candidates(prediction, market_lines, prob_source="negbin")
    totals = next(c for c in candidates if c["market"] == "totals")

    over_negbin, under_negbin = negbin_totals_prob(3.5, 5.0, NEGBIN_DISPERSION, 8.5)
    expected_prob = over_negbin if totals["selection"] == "over" else under_negbin

    assert totals["prob_source"] == "negbin"
    assert abs(totals["model_prob"] - expected_prob) < 1e-9

    over_poisson, under_poisson = totals_prob(3.5, 5.0, 8.5)
    expected_poisson = over_poisson if totals["selection"] == "over" else under_poisson
    assert abs(expected_prob - expected_poisson) > 1e-6


def test_totals_uses_skellam_probabilities_when_prob_source_is_skellam():
    prediction = _prediction(home_proj_runs=3.5, away_proj_runs=5.0)
    market_lines = {
        "totals": {"line": 8.5, "over_odds": -110, "under_odds": -110, "over_novig": None, "under_novig": None},
    }
    candidates = generate_pick_candidates(prediction, market_lines, prob_source="skellam")
    totals = next(c for c in candidates if c["market"] == "totals")

    over_poisson, under_poisson = totals_prob(3.5, 5.0, 8.5)
    expected_prob = over_poisson if totals["selection"] == "over" else under_poisson

    assert totals["prob_source"] == "skellam"
    assert abs(totals["model_prob"] - expected_prob) < 1e-9


def test_run_line_and_totals_fall_back_to_skellam_when_heuristic_requested():
    # PICK_PROBABILITY_SOURCE="heuristic" no tiene versión propia de estos
    # mercados -- ya cubierto arriba para prob_source, aquí se confirma
    # además que el NÚMERO usado es realmente el de Skellam, no NB2.
    prediction = _prediction(home_proj_runs=3.5, away_proj_runs=5.0)
    market_lines = {
        "run_line": {"line": 1.5, "home_odds": 150, "away_odds": -180, "home_novig": None, "away_novig": None},
        "totals": {"line": 8.5, "over_odds": -110, "under_odds": -110, "over_novig": None, "under_novig": None},
    }
    candidates = generate_pick_candidates(prediction, market_lines, prob_source="heuristic")

    rl = next(c for c in candidates if c["market"] == "run_line")
    home_cover_skellam, away_cover_skellam = run_line_prob(3.5, 5.0, 1.5)
    expected_rl = home_cover_skellam if rl["selection"] == "home" else away_cover_skellam
    assert abs(rl["model_prob"] - expected_rl) < 1e-9

    totals = next(c for c in candidates if c["market"] == "totals")
    over_poisson, under_poisson = totals_prob(3.5, 5.0, 8.5)
    expected_totals = over_poisson if totals["selection"] == "over" else under_poisson
    assert abs(totals["model_prob"] - expected_totals) < 1e-9


# --- directional_discrepancy con valores REALES (numpy.bool_ vs bool nativo) ---
#
# Las pruebas test_directional_discrepancy_* de arriba usan la fixture
# _prediction(), que solo tiene floats de Python escritos a mano -- pasan
# aunque directional_discrepancy no esté envuelto en bool(), porque nunca
# ejercitan la ruta real vía scipy (comparar dos floats de Python siempre da
# bool nativo). Las de aquí alimentan home_skellam_prob/home_negbin_prob con
# la salida REAL de skellam_win_prob()/negbin_win_prob() (numpy.float64) --
# la misma ruta que sigue el pipeline en producción -- y verifican el tipo
# explícitamente, no solo el valor, para que un futuro bool() removido por
# accidente vuelva a fallar aquí (mismo patrón que
# tests/test_model_agreement_real.py para mu_family_internal_disagreement).

def test_directional_discrepancy_is_native_bool_and_true_with_real_skellam_values():
    mu_home, mu_away = 3.0, 5.5  # Skellam real desfavorece al local
    home_skellam_prob = skellam_win_prob(mu_home, mu_away)
    away_skellam_prob = 1.0 - home_skellam_prob
    assert home_skellam_prob < 0.5

    # Heurístico a propósito del lado contrario -- discrepancia real, no inventada.
    prediction = _prediction(home_model_prob=0.60, away_model_prob=0.40,
                              home_skellam_prob=home_skellam_prob, away_skellam_prob=away_skellam_prob,
                              home_proj_runs=mu_home, away_proj_runs=mu_away)
    candidates = generate_pick_candidates(prediction, {
        "moneyline": {"home_odds": 130, "away_odds": -150, "home_novig": 0.40, "away_novig": 0.60},
    })
    ml = next(c for c in candidates if c["market"] == "moneyline")

    assert type(ml["directional_discrepancy"]) is bool  # no numpy.bool_
    assert ml["directional_discrepancy"] is True


def test_directional_discrepancy_is_native_bool_and_false_when_real_skellam_values_agree():
    mu_home, mu_away = 5.5, 3.0  # Skellam real favorece al local
    home_skellam_prob = skellam_win_prob(mu_home, mu_away)
    away_skellam_prob = 1.0 - home_skellam_prob
    assert home_skellam_prob > 0.5

    # Heurístico coincide con Skellam -- sin discrepancia.
    prediction = _prediction(home_model_prob=0.60, away_model_prob=0.40,
                              home_skellam_prob=home_skellam_prob, away_skellam_prob=away_skellam_prob,
                              home_proj_runs=mu_home, away_proj_runs=mu_away)
    candidates = generate_pick_candidates(prediction, {
        "moneyline": {"home_odds": -150, "away_odds": 130, "home_novig": 0.60, "away_novig": 0.40},
    })
    ml = next(c for c in candidates if c["market"] == "moneyline")

    assert type(ml["directional_discrepancy"]) is bool
    assert ml["directional_discrepancy"] is False


def test_directional_discrepancy_is_native_bool_and_true_with_real_negbin_values():
    mu_home, mu_away = 3.0, 5.5  # NB2 real desfavorece al local
    home_negbin_prob = negbin_win_prob(mu_home, mu_away, NEGBIN_DISPERSION)
    away_negbin_prob = 1.0 - home_negbin_prob
    assert home_negbin_prob < 0.5

    prediction = _prediction(home_model_prob=0.60, away_model_prob=0.40,
                              home_negbin_prob=home_negbin_prob, away_negbin_prob=away_negbin_prob,
                              home_proj_runs=mu_home, away_proj_runs=mu_away)
    candidates = generate_pick_candidates(prediction, {
        "moneyline": {"home_odds": 130, "away_odds": -150, "home_novig": 0.40, "away_novig": 0.60},
    }, prob_source="negbin")
    ml = next(c for c in candidates if c["market"] == "moneyline")

    assert type(ml["directional_discrepancy"]) is bool
    assert ml["directional_discrepancy"] is True
