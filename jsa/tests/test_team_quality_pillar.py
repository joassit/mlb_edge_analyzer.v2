"""`engine/pillars/team_quality.py` -- lesiones clave + closer_available
(@1.0.0) y la senal defensiva de fielding% agregada en @1.1.0. Cubre la
combinacion de las tres senales, no solo cada una aislada."""

from __future__ import annotations

import datetime

from jsa.domain.models import build_game_snapshot
from jsa.engine.pillars.team_quality import evaluate


def _snap(**overrides):
    fields = dict(game_id="g1", game_date=datetime.date(2026, 7, 13), season=2026, home_team="NYY", away_team="BOS")
    fields.update(overrides)
    return build_game_snapshot(**fields)


def test_no_signals_is_neutral():
    snap = _snap()
    advantage = evaluate(snap)
    assert advantage.advantage == 0


def test_away_key_injuries_favor_home():
    snap = _snap(away_key_injuries=["Star Hitter"])
    advantage = evaluate(snap)
    assert advantage.advantage == 1


def test_home_closer_unavailable_favors_away():
    snap = _snap(home_closer_available=False, away_closer_available=True)
    advantage = evaluate(snap)
    assert advantage.advantage == -1


def test_fielding_pct_advantage_favors_better_defense():
    snap = _snap(home_fielding_pct=0.992, away_fielding_pct=0.970)
    advantage = evaluate(snap)
    assert advantage.advantage == 1
    assert "Fielding%" in advantage.explanation


def test_fielding_pct_small_difference_does_not_move_advantage():
    # 0.001 de diferencia es mucho menor a _FIELDING_PCT_UNIT (0.006) -- no
    # deberia mover nada por si sola.
    snap = _snap(home_fielding_pct=0.987, away_fielding_pct=0.986)
    advantage = evaluate(snap)
    assert advantage.advantage == 0


def test_fielding_pct_none_for_either_team_is_ignored():
    snap = _snap(home_fielding_pct=0.992, away_fielding_pct=None)
    advantage = evaluate(snap)
    assert advantage.advantage == 0
    assert "Fielding%" not in advantage.explanation


def test_fielding_signal_never_dominates_alone_beyond_one_level():
    # Una diferencia enorme de fielding% (irrealista, pero prueba el cap)
    # nunca deberia mover mas de 1 nivel por si sola.
    snap = _snap(home_fielding_pct=1.000, away_fielding_pct=0.900)
    advantage = evaluate(snap)
    assert advantage.advantage == 1


def test_injuries_and_fielding_combine_additively_within_range():
    # 2 lesiones clave de diferencia (+2, saturado) + defensa mejor en home
    # (+1) se combinan pero quedan acotados a +2 (rango del contrato).
    snap = _snap(away_key_injuries=["A", "B"], home_fielding_pct=0.992, away_fielding_pct=0.970)
    advantage = evaluate(snap)
    assert advantage.advantage == 2


def test_fielding_signal_can_offset_a_single_injury_level():
    # 1 lesion clave de diferencia favorece a home (+1), pero peor defensa
    # de home (-1) los cancela.
    snap = _snap(away_key_injuries=["A"], home_fielding_pct=0.970, away_fielding_pct=0.992)
    advantage = evaluate(snap)
    assert advantage.advantage == 0


def test_pillar_contract_version_is_1_1_0():
    snap = _snap()
    advantage = evaluate(snap)
    assert advantage.pillar_contract_version == "team_quality@1.1.0"
