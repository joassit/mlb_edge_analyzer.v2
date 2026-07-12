"""
Pruebas de historical_engine/recent_form.py -- calculadoras de ventana
móvil (funciones puras sobre filas) y el experimento completo contra una
DB sembrada, sin red ni producción.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config as production_config
import historical_engine.db as historical_db
from historical_engine.recent_form import (
    windowed_team_ops, windowed_pitcher_era, windowed_bullpen_era,
    evaluate_recent_form, MIN_WINDOW_TEAM_PA,
)


class _BatRow:
    def __init__(self, game_date, at_bats, hits, doubles=0, triples=0,
                 home_runs=0, walks=0, hit_by_pitch=0, sac_flies=0):
        self.game_date = game_date
        self.at_bats = at_bats
        self.hits = hits
        self.doubles = doubles
        self.triples = triples
        self.home_runs = home_runs
        self.walks = walks
        self.hit_by_pitch = hit_by_pitch
        self.sac_flies = sac_flies


class _PitchRow:
    def __init__(self, game_date, innings_pitched, earned_runs):
        self.game_date = game_date
        self.innings_pitched = innings_pitched
        self.earned_runs = earned_runs


# --- windowed_team_ops ---

def test_windowed_team_ops_computes_obp_plus_slg_from_components():
    # Un solo juego gigante para superar el mínimo de PA con números redondos:
    # AB=400, H=100 (80 sencillos, 10 dobles, 5 triples, 5 HR), BB=40, HBP=5, SF=5.
    rows = [_BatRow("2024-05-10", at_bats=400, hits=100, doubles=10, triples=5,
                    home_runs=5, walks=40, hit_by_pitch=5, sac_flies=5)]
    ops = windowed_team_ops(rows, as_of_date="2024-05-15", window_days=15)

    pa = 400 + 40 + 5 + 5
    obp = (100 + 40 + 5) / pa
    slg = (100 + 10 + 2 * 5 + 3 * 5) / 400
    assert ops == pytest.approx(obp + slg)


def test_windowed_team_ops_excludes_games_outside_the_window():
    inside = _BatRow("2024-05-10", at_bats=400, hits=120, walks=40, hit_by_pitch=5, sac_flies=5)
    before_window = _BatRow("2024-04-01", at_bats=400, hits=40)   # equipo helado, fuera de ventana
    on_game_day = _BatRow("2024-05-15", at_bats=400, hits=40)     # el DÍA del juego: fuga, debe excluirse

    with_noise = windowed_team_ops([inside, before_window, on_game_day], "2024-05-15", 15)
    clean = windowed_team_ops([inside], "2024-05-15", 15)
    assert with_noise == pytest.approx(clean)


def test_windowed_team_ops_returns_none_below_min_pa():
    rows = [_BatRow("2024-05-10", at_bats=30, hits=10, walks=3)]  # ~33 PA < mínimo
    assert (30 + 3) < MIN_WINDOW_TEAM_PA
    assert windowed_team_ops(rows, "2024-05-15", 15) is None


# --- windowed_pitcher_era ---

def test_windowed_pitcher_era_computes_nine_er_per_ip():
    rows = [
        _PitchRow("2024-05-05", innings_pitched=6.0, earned_runs=2),
        _PitchRow("2024-05-11", innings_pitched=6.0, earned_runs=4),
    ]
    era = windowed_pitcher_era(rows, as_of_date="2024-05-15", window_days=15)
    assert era == pytest.approx(9.0 * 6 / 12.0)  # 4.50


def test_windowed_pitcher_era_returns_none_below_min_ip():
    rows = [_PitchRow("2024-05-11", innings_pitched=5.0, earned_runs=1)]  # < 8 IP
    assert windowed_pitcher_era(rows, "2024-05-15", 15) is None


def test_windowed_pitcher_era_excludes_game_day_appearance():
    rows = [
        _PitchRow("2024-05-05", innings_pitched=9.0, earned_runs=1),
        _PitchRow("2024-05-15", innings_pitched=1.0, earned_runs=9),  # el día del juego: fuga
    ]
    era = windowed_pitcher_era(rows, "2024-05-15", 15)
    assert era == pytest.approx(1.0)


# --- windowed_bullpen_era ---

def test_windowed_bullpen_era_aggregates_roster_pitchers_weighted_by_ip():
    logs = {
        1: [_PitchRow("2024-05-08", innings_pitched=10.0, earned_runs=2)],
        2: [_PitchRow("2024-05-09", innings_pitched=10.0, earned_runs=6)],
        3: [_PitchRow("2024-05-09", innings_pitched=10.0, earned_runs=0)],  # NO está en el roster
    }
    era = windowed_bullpen_era([1, 2], logs, as_of_date="2024-05-15", window_days=15)
    assert era == pytest.approx(9.0 * 8 / 20.0)  # 3.60 -- el pitcher 3 no cuenta


def test_windowed_bullpen_era_returns_none_below_min_ip():
    logs = {1: [_PitchRow("2024-05-09", innings_pitched=5.0, earned_runs=1)]}
    assert windowed_bullpen_era([1], logs, "2024-05-15", 15) is None


# --- evaluate_recent_form (experimento completo, DB sembrada) ---

def _seeded_full_db(tmp_path, name):
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db")
    historical_db.HistoricalBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    winners = ["home", "away", "home", "home", "away", "home"]
    for game_pk, winner in enumerate(winners, start=1):
        game_date = f"2024-06-{10 + game_pk:02d}"
        session.add(historical_db.HistoricalGame(
            run_id=1, game_pk=game_pk, game_date=game_date, season_year=2024,
            away_team="A", home_team="B", away_team_id=100, home_team_id=200,
            away_pitcher_id=11, home_pitcher_id=22, winner=winner,
        ))
        session.add(historical_db.HistoricalAnalysis(
            run_id=1, game_pk=game_pk, game_date=game_date, season_year=2024, as_of_date=game_date,
            away_era=3.50, home_era=4.20, away_ops=0.750, home_ops=0.730,
            away_bullpen_era=4.00, home_bullpen_era=4.40, park_factor=1.0, temp_f=72.0,
        ))

    # Logs crudos de mayo-junio: forma reciente CALIENTE para el equipo
    # visitante (OPS de ventana >> acumulado) y fría para el local.
    for day in range(1, 20):
        session.add(historical_db.HistoricalRawBattingLog(
            team_id=100, season_year=2024, game_date=f"2024-06-{day:02d}", game_pk=1000 + day,
            at_bats=35, hits=14, doubles=3, home_runs=2, walks=4, hit_by_pitch=0, sac_flies=1,
        ))
        session.add(historical_db.HistoricalRawBattingLog(
            team_id=200, season_year=2024, game_date=f"2024-06-{day:02d}", game_pk=2000 + day,
            at_bats=35, hits=6, doubles=1, home_runs=0, walks=2, hit_by_pitch=0, sac_flies=1,
        ))
        session.add(historical_db.HistoricalRawPitchingLog(
            pitcher_id=11, season_year=2024, game_date=f"2024-06-{day:02d}", game_pk=1000 + day,
            innings_pitched=1.0, earned_runs=0,
        ))
        session.add(historical_db.HistoricalRawPitchingLog(
            pitcher_id=22, season_year=2024, game_date=f"2024-06-{day:02d}", game_pk=2000 + day,
            innings_pitched=1.0, earned_runs=1,
        ))
        for team_id, bullpen_pid in ((100, 31), (200, 32)):
            session.add(historical_db.HistoricalRawPitchingLog(
                pitcher_id=bullpen_pid, season_year=2024, game_date=f"2024-06-{day:02d}",
                game_pk=team_id * 100 + day, innings_pitched=2.0, earned_runs=1,
            ))
    # Roster activo por fecha de juego (solo las fechas de los 6 juegos).
    for game_pk in range(1, 7):
        game_date = f"2024-06-{10 + game_pk:02d}"
        session.add(historical_db.HistoricalRawRosterSnapshot(
            team_id=100, season_year=2024, as_of_date=game_date, pitcher_id=31,
        ))
        session.add(historical_db.HistoricalRawRosterSnapshot(
            team_id=200, season_year=2024, as_of_date=game_date, pitcher_id=32,
        ))
    session.commit()
    session.close()
    return Session


def test_evaluate_recent_form_never_modifies_production_and_marks_applied_false(tmp_path):
    original_alpha = production_config.SKELLAM_SHRINKAGE_ALPHA
    original_weight = production_config.STARTER_WEIGHT

    Session = _seeded_full_db(tmp_path, "recent_form_applied_false")
    result = evaluate_recent_form(
        season_year=2024, run_id=1, window_days=15, blend_weights=[0.5], session_factory=Session,
    )
    assert result["applied"] is False
    assert production_config.SKELLAM_SHRINKAGE_ALPHA == original_alpha
    assert production_config.STARTER_WEIGHT == original_weight

    session = Session()
    sims = session.query(historical_db.HistoricalSimulation).all()
    session.close()
    assert len(sims) == 1
    assert sims[0].applied is False
    assert sims[0].param_name == "RECENT_FORM_BLEND_W15D"


def test_evaluate_recent_form_blend_weight_changes_the_probabilities(tmp_path):
    # La forma reciente sembrada es MUY distinta del acumulado (visitante
    # caliente, local frío) -- un peso > 0 tiene que mover el Brier respecto
    # del baseline (en alguna dirección; que mejore o no es del experimento
    # real, no de esta prueba).
    Session = _seeded_full_db(tmp_path, "recent_form_moves")
    result = evaluate_recent_form(
        season_year=2024, run_id=1, window_days=15, blend_weights=[1.0], session_factory=Session,
    )
    assert result["baseline_n_sample"] == 6
    candidate = result["proposals"][0]
    assert candidate["n_sample"] == 6
    assert candidate["brier_score"] != result["baseline_brier_score"]


def test_evaluate_recent_form_zero_weight_reproduces_baseline(tmp_path):
    # w=0.0 es la identidad: misma probabilidad que el baseline, Brier igual.
    Session = _seeded_full_db(tmp_path, "recent_form_identity")
    result = evaluate_recent_form(
        season_year=2024, run_id=1, window_days=15, blend_weights=[0.0], session_factory=Session,
    )
    assert result["proposals"][0]["brier_score"] == pytest.approx(result["baseline_brier_score"])
