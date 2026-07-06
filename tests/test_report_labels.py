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
