"""
Pruebas de model/picks.py — generación y selección de picks recomendados,
sin tocar red ni base de datos.
"""

from model.picks import generate_pick_candidates, select_picks_for_game


def _prediction(**overrides) -> dict:
    base = {
        "home_model_prob": 0.40, "away_model_prob": 0.60,
        "home_proj_runs": 3.5, "away_proj_runs": 5.0,
    }
    base.update(overrides)
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
