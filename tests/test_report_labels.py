"""
Pruebas de reports/generate_report.py -- el formato de etiquetas de picks
(team_label) y el ensamblado de las dos secciones del reporte diario.
"""

from reports.generate_report import team_label, print_yesterday_review, print_report


_GAME = {"away_team": "Tampa Bay Rays", "home_team": "Philadelphia Phillies"}


def test_team_label_moneyline_home():
    pick = {"market": "moneyline", "selection": "home", "line": None}
    assert team_label(pick, _GAME) == "Phillies ML"


def test_team_label_moneyline_away():
    pick = {"market": "moneyline", "selection": "away", "line": None}
    assert team_label(pick, _GAME) == "Rays ML"


def test_team_label_run_line_home_uses_negative_sign():
    pick = {"market": "run_line", "selection": "home", "line": 1.5}
    assert team_label(pick, _GAME) == "Phillies RL -1.5"


def test_team_label_run_line_away_uses_positive_sign():
    pick = {"market": "run_line", "selection": "away", "line": 1.5}
    assert team_label(pick, _GAME) == "Rays RL +1.5"


def test_team_label_run_line_defaults_line_to_one_point_five():
    pick = {"market": "run_line", "selection": "home", "line": None}
    assert team_label(pick, _GAME) == "Phillies RL -1.5"


def test_team_label_totals_over():
    pick = {"market": "totals", "selection": "over", "line": 8.5}
    assert team_label(pick, _GAME) == "Over 8.5"


def test_team_label_totals_under():
    pick = {"market": "totals", "selection": "under", "line": 8.5}
    assert team_label(pick, _GAME) == "Under 8.5"


def test_team_label_applies_to_forced_picks_too():
    # forced=True no cambia el formato de la etiqueta -- solo el llamador
    # decide si además anota "(forzado)" aparte.
    pick = {"market": "moneyline", "selection": "home", "line": None, "forced": True}
    assert team_label(pick, _GAME) == "Phillies ML"


def test_team_label_falls_back_to_last_word_for_unknown_team():
    pick = {"market": "moneyline", "selection": "home", "line": None}
    game = {"away_team": "Some New Expansion Team", "home_team": "Some New Expansion Team"}
    assert team_label(pick, game) == "Team ML"


def test_print_yesterday_review_handles_none_review(capsys):
    print_yesterday_review(None)
    out = capsys.readouterr().out
    assert "REVISIÓN DEL DÍA ANTERIOR" in out
    assert "Sin datos de ayer para revisar" in out


def test_print_yesterday_review_handles_zero_games(capsys):
    review = {"review_date": "2026-07-05", "n_games": 0, "games": [], "by_market": {}, "brier_score": None}
    print_yesterday_review(review)
    out = capsys.readouterr().out
    assert "REVISIÓN DEL 2026-07-05" in out
    assert "Sin datos de ayer para revisar" in out


def test_print_yesterday_review_prints_team_names_and_outcomes(capsys):
    review = {
        "review_date": "2026-07-05",
        "n_games": 1,
        "games": [{
            "away_team": "Tampa Bay Rays", "home_team": "Philadelphia Phillies",
            "away_score": 2, "home_score": 5,
            "actual_margin": 3, "actual_total": 7,
            "proj_margin": 1.2, "proj_total": 8.1,
            "picks": {
                "moneyline": {"market": "moneyline", "selection": "home", "line": None,
                              "model_prob": 0.61, "forced": False, "result": "win", "profit_unit": 0.8},
                "run_line": None,
                "totals": None,
            },
        }],
        "by_market": {
            "moneyline": {"real": {"n_picks": 1, "win_rate": 1.0, "roi": 0.8},
                          "forced": {"n_picks": 0, "win_rate": None, "roi": None}},
            "run_line": {"real": {"n_picks": 0, "win_rate": None, "roi": None},
                         "forced": {"n_picks": 0, "win_rate": None, "roi": None}},
            "totals": {"real": {"n_picks": 0, "win_rate": None, "roi": None},
                       "forced": {"n_picks": 0, "win_rate": None, "roi": None}},
        },
        "brier_score": 0.15,
    }
    print_yesterday_review(review)
    out = capsys.readouterr().out
    assert "Tampa Bay Rays @ Philadelphia Phillies" in out
    assert "Phillies ML" in out
    assert "ACERTÓ" in out
    assert "sin pick" in out  # run_line/totals sin cuotas cargadas
    assert "Brier Score del día: 0.1500" in out


def test_print_report_uses_team_label_for_picks(capsys):
    rows = [{
        "game_pk": 1, "away_team": "Tampa Bay Rays", "home_team": "Philadelphia Phillies",
        "away_pitcher": None, "home_pitcher": None,
        "away_model_prob": 0.4, "home_model_prob": 0.6,
    }]
    picks_by_game = {1: [{"market": "moneyline", "selection": "home", "line": None,
                          "edge": 0.05, "ev": 0.1, "forced": False}]}
    print_report(rows, picks_by_game=picks_by_game)
    out = capsys.readouterr().out
    assert "Phillies ML" in out
    assert "PREDICCIONES DE HOY" in out


def _row_with_models(away_model_prob, away_skellam_prob, away_negbin_prob):
    return {
        "game_pk": 1, "away_team": "A", "home_team": "B",
        "away_pitcher": None, "home_pitcher": None,
        "away_model_prob": away_model_prob, "home_model_prob": 1 - away_model_prob,
        "away_skellam_prob": away_skellam_prob, "home_skellam_prob": 1 - away_skellam_prob,
        "away_negbin_prob": away_negbin_prob, "home_negbin_prob": 1 - away_negbin_prob,
        "away_proj_runs": 4.0, "home_proj_runs": 4.0,
    }


def test_report_shows_agreement_when_heuristic_and_mu_family_favor_same_side(capsys):
    # Heurístico y Skellam favorecen al visitante; NB2 también coincide
    # internamente con Skellam -- acuerdo total, sin avisos.
    row = _row_with_models(away_model_prob=0.60, away_skellam_prob=0.65, away_negbin_prob=0.62)
    print_report([row])
    out = capsys.readouterr().out
    assert "✅ heurístico y familia Skellam/NB2 coinciden en el favorito" in out
    assert "DISCREPA" not in out
    assert "🔀" not in out


def test_report_shows_heuristic_vs_mu_family_disagreement(capsys):
    # Heurístico favorece al local; Skellam (y NB2, de acuerdo entre sí)
    # favorecen al visitante -- discrepancia heurístico vs. familia mu.
    row = _row_with_models(away_model_prob=0.45, away_skellam_prob=0.60, away_negbin_prob=0.58)
    print_report([row])
    out = capsys.readouterr().out
    assert "⚠️  heurístico DISCREPA de la familia Skellam/NB2" in out
    assert "coinciden en el favorito" not in out


def test_report_shows_internal_skellam_negbin_disagreement(capsys):
    # Caso raro: Skellam favorece al visitante, NB2 favorece al local --
    # debe aparecer el aviso aparte, sin importar qué diga el heurístico.
    row = _row_with_models(away_model_prob=0.60, away_skellam_prob=0.51, away_negbin_prob=0.49)
    print_report([row])
    out = capsys.readouterr().out
    assert "🔀 Skellam y NB2 discrepan entre sí" in out


# --- Visibilidad de juegos descartados en el reporte (Sección 2) ---

def test_print_report_shows_discarded_game_message(capsys):
    rows = [{
        "game_pk": 1, "away_team": "A", "home_team": "B",
        "away_pitcher": None, "home_pitcher": None,
        "away_model_prob": 0.4, "home_model_prob": 0.6,
    }]
    discarded = [{"away_team": "Philadelphia Phillies", "home_team": "Kansas City Royals",
                  "message": "Philadelphia Phillies @ Kansas City Royals -- ya estaba en curso "
                             "(estado: In Progress), no se generó predicción."}]
    print_report(rows, discarded_games=discarded)
    out = capsys.readouterr().out
    assert "⏱️ 1 juego no procesado:" in out
    assert "Philadelphia Phillies @ Kansas City Royals" in out
    assert "ya estaba en curso" in out


def test_print_report_lists_multiple_discarded_games(capsys):
    rows = [{
        "game_pk": 1, "away_team": "A", "home_team": "B",
        "away_pitcher": None, "home_pitcher": None,
        "away_model_prob": 0.4, "home_model_prob": 0.6,
    }]
    discarded = [
        {"away_team": "C", "home_team": "D", "message": "C @ D -- pospuesto (estado: Postponed), no se generó predicción."},
        {"away_team": "E", "home_team": "F", "message": "E @ F -- ya estaba en curso (estado: In Progress), no se generó predicción."},
    ]
    print_report(rows, discarded_games=discarded)
    out = capsys.readouterr().out
    assert "⏱️ 2 juegos no procesados:" in out
    assert "C @ D -- pospuesto" in out
    assert "E @ F -- ya estaba en curso" in out


def test_print_report_shows_no_discard_note_when_there_are_none(capsys):
    rows = [{
        "game_pk": 1, "away_team": "A", "home_team": "B",
        "away_pitcher": None, "home_pitcher": None,
        "away_model_prob": 0.4, "home_model_prob": 0.6,
    }]
    print_report(rows, discarded_games=None)
    out = capsys.readouterr().out
    assert "⏱️" not in out
    assert "no procesado" not in out


def test_print_report_shows_discard_note_even_with_no_games_processed(capsys):
    # Si TODOS los juegos del día se descartaron (rows vacío), el reporte
    # debe seguir explicando por qué -- no solo decir "no hay juegos".
    discarded = [{"away_team": "A", "home_team": "B",
                  "message": "A @ B -- pospuesto (estado: Postponed), no se generó predicción."}]
    print_report([], discarded_games=discarded)
    out = capsys.readouterr().out
    assert "⏱️ 1 juego no procesado:" in out
    assert "No hay juegos analizados hoy." in out


# --- Nota de fase de calibración (config.MIN_LIQUIDATED_PICKS_FOR_CALIBRATION) ---

def test_print_report_shows_calibration_note_when_provided(capsys):
    rows = [{
        "game_pk": 1, "away_team": "A", "home_team": "B",
        "away_pitcher": None, "home_pitcher": None,
        "away_model_prob": 0.4, "home_model_prob": 0.6,
    }]
    print_report(rows, calibration_note="🧪 Fase de calibración: 34/200 juegos evaluados con resultado real.")
    out = capsys.readouterr().out
    assert "🧪 Fase de calibración: 34/200" in out


def test_print_report_shows_calibration_note_even_with_no_games_and_no_discards(capsys):
    # Un día sin juegos (all-star break, etc.) no debe esconder la nota de
    # calibración -- es informativa sobre el histórico acumulado, no
    # depende de que haya juegos hoy.
    print_report([], calibration_note="🧪 Fase de calibración: 10/200 juegos evaluados con resultado real.")
    out = capsys.readouterr().out
    assert "🧪 Fase de calibración: 10/200" in out
    assert "No hay juegos analizados hoy." in out


def test_print_report_shows_no_calibration_note_when_none(capsys):
    rows = [{
        "game_pk": 1, "away_team": "A", "home_team": "B",
        "away_pitcher": None, "home_pitcher": None,
        "away_model_prob": 0.4, "home_model_prob": 0.6,
    }]
    print_report(rows, calibration_note=None)
    out = capsys.readouterr().out
    assert "🧪" not in out
    assert "calibración" not in out


# --- A1: rotular la semántica del edge de GameAnalysis en el reporte ---
# GameAnalysis.away_edge/home_edge = heurístico vs. mejor cuota CON vig;
# Pick.edge (mostrado por pick abajo) = fuente configurada vs. consenso
# SIN vig -- dos cálculos distintos que comparten el nombre "edge", sin
# ninguna aclaración en el reporte de cuál es cuál.

def _row_with_market_edge():
    return {
        "game_pk": 1, "away_team": "A", "home_team": "B",
        "away_pitcher": None, "home_pitcher": None,
        "away_model_prob": 0.55, "home_model_prob": 0.45,
        "away_market_prob": 0.50, "home_market_prob": 0.52,
        "away_edge": 0.05, "home_edge": -0.07,
        "away_ev": 0.10, "home_ev": -0.12,
    }


def test_print_report_labels_game_level_edge_as_heuristic_vs_vig(capsys):
    print_report([_row_with_market_edge()])
    out = capsys.readouterr().out
    assert "heurístico" in out.lower()
    assert "con vig" in out.lower()


# --- Auditabilidad de momios: momio crudo, fuente y antigüedad de la
# cuota, tanto a nivel de partido como de cada pick recomendado, y del
# momio de apertura en la revisión de resultados de ayer. ---

def test_print_report_shows_raw_odds_source_and_capture_time_when_available(capsys):
    from datetime import datetime
    row = _row_with_market_edge()
    row.update({
        "away_odds": 130, "home_odds": -150,
        "market_price_source": "api_live",
        "market_captured_at": datetime(2026, 7, 7, 12, 0, 0),
    })
    print_report([row])
    out = capsys.readouterr().out
    assert "Momio" in out
    assert "+130" in out
    assert "-150" in out
    assert "API en vivo" in out
    assert "2026-07-07 12:00" in out
    assert "antigüedad" in out.lower()
    assert "Dato no disponible" not in out


def test_print_report_shows_dato_no_disponible_when_source_and_capture_missing(capsys):
    row = _row_with_market_edge()  # sin away_odds/market_price_source/market_captured_at
    print_report([row])
    out = capsys.readouterr().out
    assert "Dato no disponible" in out


def test_print_report_does_not_duplicate_existing_market_lines(capsys):
    row = _row_with_market_edge()
    row.update({"away_odds": 130, "home_odds": -150, "market_price_source": "manual"})
    print_report([row])
    out = capsys.readouterr().out
    # Las líneas que ya existían siguen presentes exactamente una vez.
    assert out.count("Mercado  ->") == 1
    assert out.count("Edge     ->") == 1
    assert out.count("EV       ->") == 1


def test_print_report_pick_line_shows_odds_implied_prob_and_kelly_placeholder(capsys):
    rows = [{
        "game_pk": 1, "away_team": "A", "home_team": "B",
        "away_pitcher": None, "home_pitcher": None,
        "away_model_prob": 0.4, "home_model_prob": 0.6,
    }]
    picks_by_game = {1: [{"market": "moneyline", "selection": "home", "line": None,
                          "edge": 0.05, "ev": 0.1, "forced": False, "odds_used": -150}]}
    print_report(rows, picks_by_game=picks_by_game)
    out = capsys.readouterr().out
    assert "momio -150" in out
    assert "prob. implícita" in out
    assert "Kelly Dato no disponible" in out


def test_print_report_pick_line_shows_dato_no_disponible_without_odds_used(capsys):
    rows = [{
        "game_pk": 1, "away_team": "A", "home_team": "B",
        "away_pitcher": None, "home_pitcher": None,
        "away_model_prob": 0.4, "home_model_prob": 0.6,
    }]
    picks_by_game = {1: [{"market": "moneyline", "selection": "home", "line": None,
                          "edge": 0.05, "ev": 0.1, "forced": False}]}  # sin odds_used
    print_report(rows, picks_by_game=picks_by_game)
    out = capsys.readouterr().out
    assert "momio Dato no disponible" in out
    assert "prob. implícita Dato no disponible" in out


def test_print_report_pick_line_shows_market_prob_actually_used_for_edge(capsys):
    # Reproduce el hallazgo de la auditoría del commit 21a30c5: con cuota
    # en vivo, "prob. implícita" (derivada SOLO del momio, CON vig) puede
    # diferir del consenso sin vig que el edge realmente usa -- incluso
    # con signo opuesto. La línea debe mostrar el número REAL usado para
    # el edge (p["market_prob"]), no solo el derivado del momio.
    rows = [{
        "game_pk": 1, "away_team": "A", "home_team": "B",
        "away_pitcher": None, "home_pitcher": None,
        "away_model_prob": 0.4, "home_model_prob": 0.6,
    }]
    # odds_used=-160 implica 61.5% con vig, pero el edge se calculó contra
    # un consenso sin vig de 58% -- deliberadamente distinto.
    picks_by_game = {1: [{"market": "moneyline", "selection": "away", "line": None,
                          "edge": 0.02, "ev": 0.1, "forced": False,
                          "odds_used": -160, "market_prob": 0.58}]}
    print_report(rows, picks_by_game=picks_by_game)
    out = capsys.readouterr().out
    assert "momio -160" in out
    assert "prob. implícita 61.5%" in out
    assert "58.0%" in out  # el market_prob real usado para el edge, no 61.5%
    assert "usada para el edge" in out


def test_print_report_pick_line_market_prob_shows_dato_no_disponible_for_old_picks(capsys):
    # Picks generados ANTES de este commit no traen "market_prob" en el
    # dict -- no debe inventarse ese valor ni reventar, debe leerse
    # explícitamente como no disponible.
    rows = [{
        "game_pk": 1, "away_team": "A", "home_team": "B",
        "away_pitcher": None, "home_pitcher": None,
        "away_model_prob": 0.4, "home_model_prob": 0.6,
    }]
    picks_by_game = {1: [{"market": "moneyline", "selection": "home", "line": None,
                          "edge": 0.05, "ev": 0.1, "forced": False, "odds_used": -150}]}
    assert "market_prob" not in picks_by_game[1][0]
    print_report(rows, picks_by_game=picks_by_game)
    out = capsys.readouterr().out
    assert "usada para el edge Dato no disponible" in out


def test_print_yesterday_review_shows_opening_odds_and_missing_clv_note(capsys):
    review = {
        "review_date": "2026-07-05",
        "n_games": 1,
        "games": [{
            "away_team": "Tampa Bay Rays", "home_team": "Philadelphia Phillies",
            "away_score": 2, "home_score": 5,
            "actual_margin": 3, "actual_total": 7,
            "proj_margin": 1.2, "proj_total": 8.1,
            "picks": {
                "moneyline": {"market": "moneyline", "selection": "home", "line": None,
                              "model_prob": 0.61, "forced": False, "result": "win",
                              "profit_unit": 0.8, "odds_used": -150},
                "run_line": None,
                "totals": None,
            },
        }],
        "by_market": {
            "moneyline": {"real": {"n_picks": 1, "win_rate": 1.0, "roi": 0.8},
                          "forced": {"n_picks": 0, "win_rate": None, "roi": None}},
            "run_line": {"real": {"n_picks": 0, "win_rate": None, "roi": None},
                         "forced": {"n_picks": 0, "win_rate": None, "roi": None}},
            "totals": {"real": {"n_picks": 0, "win_rate": None, "roi": None},
                       "forced": {"n_picks": 0, "win_rate": None, "roi": None}},
        },
        "brier_score": 0.15,
    }
    print_yesterday_review(review)
    out = capsys.readouterr().out
    assert "momio apertura -150" in out
    assert "Dato no disponible" in out  # nota única de cierre/CLV/movimiento
    assert out.count("Cierre / CLV / movimiento de mercado") == 1
