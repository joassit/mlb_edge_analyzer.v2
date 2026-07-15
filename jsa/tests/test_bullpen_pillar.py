"""`engine/pillars/bullpen.py` -- ERA de bullpen con shrinkage bayesiano
(@1.1.0, misma `shrunk_era()` que ya usa `starter`) + ajuste por
disponibilidad de closer. Antes de @1.1.0, bullpen era el unico de los 7
pilares que comparaba un ERA crudo, sin encoger hacia el promedio de
liga por muestra chica -- este archivo cubre especificamente ese
comportamiento nuevo, nunca testeado antes en aislamiento."""

from __future__ import annotations

import datetime

from jsa.config import LEAGUE_AVG_ERA
from jsa.domain.models import build_game_snapshot
from jsa.engine.pillars.bullpen import evaluate


def _snap(**overrides):
    fields = dict(game_id="g1", game_date=datetime.date(2026, 7, 13), season=2026, home_team="NYY", away_team="BOS")
    fields.update(overrides)
    return build_game_snapshot(**fields)


def test_no_data_is_neutral():
    snap = _snap()
    advantage = evaluate(snap)
    assert advantage.advantage == 0
    assert "sin datos" in advantage.explanation.lower()


def test_large_ip_sample_barely_shrinks():
    # Con IP grande (temporada casi completa), el ERA encogido deberia
    # quedar muy cerca del crudo -- shrinkage minimo.
    snap = _snap(
        home_bullpen_era=2.50, away_bullpen_era=5.00,
        home_bullpen_ip_sample=500.0, away_bullpen_ip_sample=500.0,
        league_avg_era=4.30,
    )
    advantage = evaluate(snap)
    # Diferencia cruda = 2.50, incluso encogida (500 IP vs k_ip=60) sigue
    # siendo grande -> advantage saturado en +2 (home mucho mejor).
    assert advantage.advantage == 2


def test_small_ip_sample_shrinks_heavily_toward_league():
    # Con IP muy chica (ej. principios de temporada), el ERA encogido
    # deberia quedar muy cerca del promedio de liga para AMBOS equipos --
    # la diferencia cruda de 4.5 carreras casi desaparece.
    snap = _snap(
        home_bullpen_era=1.00, away_bullpen_era=9.00,
        home_bullpen_ip_sample=2.0, away_bullpen_ip_sample=2.0,
        league_avg_era=4.30,
    )
    advantage = evaluate(snap)
    # Sin shrinkage esto seria un +2 facil (diferencia de 8 carreras) --
    # con IP=2 y k_ip=60, el shrinkage tira ambos casi al promedio de
    # liga, dejando una diferencia mucho mas chica.
    assert advantage.advantage in (-1, 0, 1)


def test_zero_ip_sample_uses_league_average_not_raw_era():
    # ip_sample=None (o 0) -> shrunk_era() devuelve el promedio de liga
    # sin importar el ERA crudo -- mismo comportamiento que starter.
    snap = _snap(
        home_bullpen_era=1.00, away_bullpen_era=1.00,
        home_bullpen_ip_sample=None, away_bullpen_ip_sample=None,
        league_avg_era=4.30,
    )
    advantage = evaluate(snap)
    assert advantage.advantage == 0
    assert "liga=4.30" in advantage.explanation


def test_missing_one_side_falls_back_to_league_average_not_opponent_era():
    # Cambio de comportamiento @1.1.0: antes, si un lado no tenia ERA, se
    # usaba el ERA del RIVAL (asumir paridad). Ahora, igual que starter,
    # se usa el promedio de liga -- un ERA de rival muy bajo YA NO
    # oculta la falta de dato del otro lado detras de una paridad falsa.
    snap = _snap(
        away_bullpen_era=1.50, away_bullpen_ip_sample=500.0,
        home_bullpen_era=None, home_bullpen_ip_sample=None,
        league_avg_era=4.30,
    )
    advantage = evaluate(snap)
    # away muy por debajo de liga (1.50 vs 4.30) -> favorece a away, no 0.
    assert advantage.advantage < 0


def test_closer_unavailable_penalty_still_applies_after_shrinkage():
    snap = _snap(
        home_bullpen_era=4.00, away_bullpen_era=4.00,
        home_bullpen_ip_sample=300.0, away_bullpen_ip_sample=300.0,
        home_closer_available=False, away_closer_available=True,
        league_avg_era=4.30,
    )
    advantage = evaluate(snap)
    assert advantage.advantage < 0  # penaliza a home por closer no disponible


def test_pillar_contract_version_is_1_1_0():
    snap = _snap()
    advantage = evaluate(snap)
    assert advantage.pillar_contract_version == "bullpen@1.1.0"


def test_falls_back_to_config_league_avg_era_when_snapshot_has_none():
    snap = _snap(
        home_bullpen_era=1.00, away_bullpen_era=1.00,
        home_bullpen_ip_sample=None, away_bullpen_ip_sample=None,
        league_avg_era=None,
    )
    advantage = evaluate(snap)
    assert f"liga={LEAGUE_AVG_ERA:.2f}" in advantage.explanation
