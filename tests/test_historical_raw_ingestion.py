"""
Pruebas de historical_engine/raw_ingestion.py contra HTTP mockeado (cero
red real) -- verifica que cachea gameLog de bateo/pitcheo + roster activo
UNA sola vez por entidad, y que una segunda corrida no vuelve a pedirlo
(idempotencia, el punto central de este módulo: "no quiero estar
descargando esto de nuevo cada vez").
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import historical_engine.db as historical_db
import historical_engine.raw_ingestion as raw_ingestion


class _FakeResponse:
    def __init__(self, json_data):
        self._json = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def _fresh_session(tmp_path, name: str):
    engine = create_engine(f"sqlite:///{tmp_path}/{name}.db")
    historical_db.HistoricalBase.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_games(Session, run_id=1, season_year=2024):
    session = Session()
    session.add(historical_db.HistoricalGame(
        run_id=run_id, game_pk=1, game_date="2024-05-01", season_year=season_year,
        away_team="A", home_team="B", away_team_id=100, home_team_id=200,
        away_pitcher_id=11, home_pitcher_id=22, winner="home",
    ))
    session.add(historical_db.HistoricalGame(
        run_id=run_id, game_pk=2, game_date="2024-05-02", season_year=season_year,
        away_team="B", home_team="A", away_team_id=200, home_team_id=100,
        away_pitcher_id=22, home_pitcher_id=11, winner="away",
    ))
    session.commit()
    session.close()


def _install_fake_session(monkeypatch, handler):
    fake = type("FakeSession", (), {"get": staticmethod(handler)})()
    monkeypatch.setattr(raw_ingestion, "http_session", fake)


# --- ingest_raw_batting_logs ---

def test_ingest_raw_batting_logs_fetches_once_per_team_and_stores_rows(tmp_path, monkeypatch):
    Session = _fresh_session(tmp_path, "batting_basic")
    _seed_games(Session)
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        return _FakeResponse({"stats": [{"splits": [
            {"date": "2024-05-01", "stat": {"atBats": 30, "hits": 8, "plateAppearances": 35}},
            {"date": "2024-05-02", "stat": {"atBats": 32, "hits": 10, "plateAppearances": 37}},
        ]}]})

    _install_fake_session(monkeypatch, fake_get)

    result = raw_ingestion.ingest_raw_batting_logs(2024, run_id=1, session_factory=Session)

    assert result["n_teams_fetched"] == 2  # team_id 100 y 200
    assert result["n_rows"] == 4
    assert len(calls) == 2  # UNA llamada por equipo, no por juego

    session = Session()
    rows = session.query(historical_db.HistoricalRawBattingLog).all()
    session.close()
    assert len(rows) == 4
    assert {r.team_id for r in rows} == {100, 200}


def test_ingest_raw_batting_logs_skips_teams_already_cached(tmp_path, monkeypatch):
    Session = _fresh_session(tmp_path, "batting_idempotent")
    _seed_games(Session)
    n_calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        n_calls["n"] += 1
        return _FakeResponse({"stats": [{"splits": [
            {"date": "2024-05-01", "stat": {"atBats": 30, "hits": 8}},
        ]}]})

    _install_fake_session(monkeypatch, fake_get)

    raw_ingestion.ingest_raw_batting_logs(2024, run_id=1, session_factory=Session)
    assert n_calls["n"] == 2

    result = raw_ingestion.ingest_raw_batting_logs(2024, run_id=1, session_factory=Session)
    assert n_calls["n"] == 2  # segunda corrida: CERO llamadas nuevas
    assert result["n_teams_fetched"] == 0
    assert result["n_teams_skipped_already_cached"] == 2


def test_ingest_raw_batting_logs_counts_errors_without_crashing(tmp_path, monkeypatch):
    Session = _fresh_session(tmp_path, "batting_errors")
    _seed_games(Session)

    def fake_get(url, params=None, timeout=None):
        return _FakeResponse({"stats": []})  # payload sin splits -> KeyError/IndexError

    _install_fake_session(monkeypatch, fake_get)

    result = raw_ingestion.ingest_raw_batting_logs(2024, run_id=1, session_factory=Session)
    assert result["n_errors"] == 2
    assert result["n_teams_fetched"] == 0


# --- ingest_raw_roster_snapshots ---

def test_ingest_raw_roster_snapshots_requests_date_minus_one_day(tmp_path, monkeypatch):
    Session = _fresh_session(tmp_path, "roster_date_cutoff")
    _seed_games(Session)
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        return _FakeResponse({"roster": [{"person": {"id": 1}, "position": {"abbreviation": "P"}}]})

    _install_fake_session(monkeypatch, fake_get)

    raw_ingestion.ingest_raw_roster_snapshots(2024, run_id=1, session_factory=Session)

    dates_requested = {p["date"] for _, p in calls}
    assert dates_requested == {"2024-04-30", "2024-05-01"}  # game_date - 1 día
    assert all(p["rosterType"] == "active" for _, p in calls)


def test_ingest_raw_roster_snapshots_ignores_non_pitchers(tmp_path, monkeypatch):
    Session = _fresh_session(tmp_path, "roster_non_pitchers")
    _seed_games(Session)

    def fake_get(url, params=None, timeout=None):
        return _FakeResponse({"roster": [
            {"person": {"id": 1}, "position": {"abbreviation": "P"}},
            {"person": {"id": 2}, "position": {"abbreviation": "1B"}},
        ]})

    _install_fake_session(monkeypatch, fake_get)
    raw_ingestion.ingest_raw_roster_snapshots(2024, run_id=1, session_factory=Session)

    session = Session()
    pitcher_ids = {r.pitcher_id for r in session.query(historical_db.HistoricalRawRosterSnapshot).all()}
    session.close()
    assert pitcher_ids == {1}


def test_ingest_raw_roster_snapshots_deduplicates_doubleheader_dates(tmp_path, monkeypatch):
    # Dos juegos el mismo día para el mismo equipo (misma game_date) no
    # deben generar dos llamadas al roster -- mismo (team_id, as_of_date).
    Session = _fresh_session(tmp_path, "roster_doubleheader")
    session = Session()
    for game_pk in (1, 2):
        session.add(historical_db.HistoricalGame(
            run_id=1, game_pk=game_pk, game_date="2024-05-01", season_year=2024,
            away_team="A", home_team="B", away_team_id=100, home_team_id=200,
            away_pitcher_id=11, home_pitcher_id=22, winner="home",
        ))
    session.commit()
    session.close()

    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _FakeResponse({"roster": []})

    _install_fake_session(monkeypatch, fake_get)
    raw_ingestion.ingest_raw_roster_snapshots(2024, run_id=1, session_factory=Session)

    assert len(calls) == 2  # un roster por equipo (100 y 200), no por juego


def test_ingest_raw_roster_snapshots_skips_pairs_already_cached(tmp_path, monkeypatch):
    Session = _fresh_session(tmp_path, "roster_idempotent")
    _seed_games(Session)
    n_calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        n_calls["n"] += 1
        return _FakeResponse({"roster": []})

    _install_fake_session(monkeypatch, fake_get)
    raw_ingestion.ingest_raw_roster_snapshots(2024, run_id=1, session_factory=Session)
    first_call_count = n_calls["n"]

    result = raw_ingestion.ingest_raw_roster_snapshots(2024, run_id=1, session_factory=Session)
    assert n_calls["n"] == first_call_count  # nada nuevo
    assert result["n_snapshots_fetched"] == 0


# --- ingest_raw_pitching_logs ---

def test_ingest_raw_pitching_logs_covers_starters_and_roster_bullpen(tmp_path, monkeypatch):
    Session = _fresh_session(tmp_path, "pitching_coverage")
    _seed_games(Session)  # abridores 11 y 22

    session = Session()
    session.add(historical_db.HistoricalRawRosterSnapshot(
        team_id=100, season_year=2024, as_of_date="2024-05-01", pitcher_id=99,  # relevista, no abridor
    ))
    session.commit()
    session.close()

    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _FakeResponse({"stats": [{"splits": [
            {"date": "2024-05-01", "stat": {"inningsPitched": "6.1", "earnedRuns": 2}},
        ]}]})

    _install_fake_session(monkeypatch, fake_get)
    result = raw_ingestion.ingest_raw_pitching_logs(2024, run_id=1, session_factory=Session)

    assert result["n_pitchers_fetched"] == 3  # 11, 22 (abridores) + 99 (bullpen)
    session = Session()
    rows = session.query(historical_db.HistoricalRawPitchingLog).all()
    session.close()
    assert {r.pitcher_id for r in rows} == {11, 22, 99}
    assert rows[0].innings_pitched == 6 + 1 / 3  # '6.1' -> 6 y 1/3, no 6.1 en base 10


def test_ingest_raw_pitching_logs_skips_pitchers_already_cached(tmp_path, monkeypatch):
    Session = _fresh_session(tmp_path, "pitching_idempotent")
    _seed_games(Session)
    n_calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        n_calls["n"] += 1
        return _FakeResponse({"stats": [{"splits": [{"date": "2024-05-01", "stat": {"inningsPitched": "5.0"}}]}]})

    _install_fake_session(monkeypatch, fake_get)
    raw_ingestion.ingest_raw_pitching_logs(2024, run_id=1, session_factory=Session)
    first_call_count = n_calls["n"]
    assert first_call_count == 2  # pitchers 11, 22

    result = raw_ingestion.ingest_raw_pitching_logs(2024, run_id=1, session_factory=Session)
    assert n_calls["n"] == first_call_count
    assert result["n_pitchers_fetched"] == 0
    assert result["n_pitchers_skipped_already_cached"] == 2


# --- ingest_raw_logs_for_season (orquestador) ---

def test_ingest_raw_logs_for_season_runs_all_three_layers(tmp_path, monkeypatch):
    Session = _fresh_session(tmp_path, "orchestrator")
    _seed_games(Session)

    def fake_get(url, params=None, timeout=None):
        if url.endswith("/roster"):
            return _FakeResponse({"roster": [{"person": {"id": 33}, "position": {"abbreviation": "P"}}]})
        return _FakeResponse({"stats": [{"splits": [{"date": "2024-05-01", "stat": {"atBats": 4, "inningsPitched": "1.0"}}]}]})

    _install_fake_session(monkeypatch, fake_get)
    result = raw_ingestion.ingest_raw_logs_for_season(2024, run_id=1, session_factory=Session)

    assert set(result.keys()) == {"batting", "roster", "pitching"}
    assert result["batting"]["n_teams_fetched"] == 2
    # 2 equipos x 2 fechas (2024-05-01, 2024-05-02) = 4 pares (team_id, as_of_date)
    assert result["roster"]["n_snapshots_fetched"] == 4
    # pitching debe incluir a los 2 abridores + el pitcher 33 visto en el roster
    assert result["pitching"]["n_pitchers_fetched"] == 3
