"""`historical/statcast_ingestion.py` -- ingesta MINIMA de eventos crudos
de bateo de Statcast (Etapa 2 del spike, ver
`jsa/docs/statcast_integration_design.md`). Nunca red real -- HTTP
mockeado. SQLite real basado en archivo (nunca `:memory:`)."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from jsa.historical import db as historical_db
from jsa.historical import statcast_ingestion as si

_CSV_HEADER = (
    "pitch_type,game_date,release_speed,player_name,batter,pitcher,events,description,zone,des,"
    "game_type,stand,p_throws,home_team,away_team,type,balls,strikes,game_year,inning,inning_topbot,"
    "launch_speed,launch_angle,game_pk,estimated_ba_using_speedangle,estimated_woba_using_speedangle,"
    "at_bat_number,pitch_number"
)


def _csv_row(*, game_pk, game_date, at_bat_number, pitch_number, inning_topbot, batter, pitcher, launch_speed, xwoba, event_type="X"):
    fields = {
        "pitch_type": "FF", "game_date": game_date, "release_speed": "95.0", "player_name": "x", "batter": batter, "pitcher": pitcher,
        "events": "single", "description": "hit_into_play", "zone": "5", "des": "x", "game_type": "R", "stand": "R", "p_throws": "R",
        "home_team": "NYY", "away_team": "BOS", "type": event_type, "balls": "1", "strikes": "1", "game_year": "2022", "inning": "1",
        "inning_topbot": inning_topbot, "launch_speed": launch_speed, "launch_angle": "20", "game_pk": game_pk,
        "estimated_ba_using_speedangle": "0.5", "estimated_woba_using_speedangle": xwoba,
        "at_bat_number": at_bat_number, "pitch_number": pitch_number,
    }
    return ",".join(str(fields[c]) for c in _CSV_HEADER.split(","))


def _fake_csv_response(rows: list[str], status_code: int = 200) -> Mock:
    resp = Mock()
    resp.status_code = status_code
    resp.raise_for_status = Mock()
    text = _CSV_HEADER + "\n" + "\n".join(rows)
    resp.text = text
    resp.content = text.encode()
    return resp


def test_parse_batted_ball_events_filters_to_type_x_only():
    rows = [
        _csv_row(game_pk=1, game_date="2022-04-10", at_bat_number=1, pitch_number=1, inning_topbot="Top", batter=100, pitcher=200, launch_speed="98.5", xwoba="0.450"),
        _csv_row(game_pk=1, game_date="2022-04-10", at_bat_number=1, pitch_number=2, inning_topbot="Top", batter=100, pitcher=200, launch_speed="", xwoba="", event_type="S"),
    ]
    events = si._parse_batted_ball_events(_CSV_HEADER + "\n" + "\n".join(rows))
    assert len(events) == 1
    assert events[0]["launch_speed"] == 98.5
    assert events[0]["xwoba"] == 0.450


def test_parse_batted_ball_events_handles_missing_values_gracefully():
    row = _csv_row(game_pk=1, game_date="2022-04-10", at_bat_number=1, pitch_number=1, inning_topbot="Bot", batter=100, pitcher=200, launch_speed="", xwoba="")
    events = si._parse_batted_ball_events(_CSV_HEADER + "\n" + row)
    assert len(events) == 1
    assert events[0]["launch_speed"] is None
    assert events[0]["xwoba"] is None


def test_date_chunks_are_contiguous_and_non_overlapping():
    chunks = si._date_chunks("2022-03-01", "2022-04-15", chunk_days=30)
    assert chunks[0] == ("2022-03-01", "2022-03-30")
    assert chunks[1][0] == "2022-03-31"  # arranca justo al dia siguiente, sin solape ni gap
    assert chunks[-1][1] == "2022-04-15"


def test_date_chunks_single_chunk_when_range_shorter_than_chunk_size():
    chunks = si._date_chunks("2022-04-01", "2022-04-08", chunk_days=30)
    assert chunks == [("2022-04-01", "2022-04-08")]


def test_fetch_batted_ball_events_for_range_handles_request_exception():
    import requests
    with patch("jsa.historical.statcast_ingestion.requests.get", side_effect=requests.RequestException("boom")):
        events, cost = si.fetch_batted_ball_events_for_range(2022, "2022-04-01", "2022-04-08")
    assert events == []
    assert cost["error"] == "boom"
    assert cost["n_batted_ball_events"] == 0


@pytest.fixture()
def hist_url(tmp_path):
    return f"sqlite:///{tmp_path}/jsa_statcast_ingestion_test.db"


def test_ingest_statcast_season_minimal_stores_events_and_reports_cost(hist_url):
    rows = [
        _csv_row(game_pk=1, game_date="2022-04-10", at_bat_number=1, pitch_number=1, inning_topbot="Top", batter=100, pitcher=200, launch_speed="98.5", xwoba="0.450"),
        _csv_row(game_pk=1, game_date="2022-04-10", at_bat_number=2, pitch_number=1, inning_topbot="Bot", batter=300, pitcher=400, launch_speed="80.0", xwoba="0.250"),
    ]
    resp = _fake_csv_response(rows)
    with patch("jsa.historical.statcast_ingestion.requests.get", return_value=resp):
        summary = si.ingest_statcast_season_minimal(2022, hist_url, chunk_days=400)  # 1 solo chunk para el test

    assert summary["skipped"] is False
    assert summary["n_batted_ball_events_fetched"] == 2 * summary["n_chunks"]  # mismo CSV mock devuelto por cada chunk
    assert summary["n_rows_actually_stored"] == 2  # deduplicado por (game_pk, at_bat_number, pitch_number)
    assert summary["total_elapsed_seconds"] >= 0
    assert "chunk_costs" in summary

    engine = historical_db.get_engine(hist_url)
    assert historical_db.count_statcast_events_for_season(engine, 2022) == 2


def test_ingest_statcast_season_minimal_skips_if_already_ingested(hist_url):
    rows = [_csv_row(game_pk=1, game_date="2022-04-10", at_bat_number=1, pitch_number=1, inning_topbot="Top", batter=100, pitcher=200, launch_speed="98.5", xwoba="0.450")]
    resp = _fake_csv_response(rows)
    with patch("jsa.historical.statcast_ingestion.requests.get", return_value=resp):
        si.ingest_statcast_season_minimal(2022, hist_url, chunk_days=400)
        second = si.ingest_statcast_season_minimal(2022, hist_url, chunk_days=400)
    assert second["skipped"] is True
    assert second["already_ingested"] == 1


def test_ingest_statcast_season_minimal_force_reingests(hist_url):
    rows = [_csv_row(game_pk=1, game_date="2022-04-10", at_bat_number=1, pitch_number=1, inning_topbot="Top", batter=100, pitcher=200, launch_speed="98.5", xwoba="0.450")]
    resp = _fake_csv_response(rows)
    with patch("jsa.historical.statcast_ingestion.requests.get", return_value=resp):
        si.ingest_statcast_season_minimal(2022, hist_url, chunk_days=400)
        forced = si.ingest_statcast_season_minimal(2022, hist_url, chunk_days=400, force=True)
    assert forced["skipped"] is False
    assert forced["n_rows_actually_stored"] == 1
