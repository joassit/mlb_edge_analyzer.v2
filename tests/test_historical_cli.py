"""
Prueba de humo del CLI (historical_engine/cli.py) para los comandos que no
requieren red (validate/compare/train/report) -- confirma que el parser y
la orquestación funcionan de punta a punta contra una base sembrada.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import historical_engine.db as historical_db
import historical_engine.validation as validation_module
import historical_engine.training as training_module
import historical_engine.runs_analysis as runs_analysis_module
from historical_engine.cli import main as cli_main


def _seed(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/cli_test.db")
    historical_db.HistoricalBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    for mod in (validation_module, training_module, runs_analysis_module):
        monkeypatch.setattr(mod, "SessionLocal", Session)

    session = Session()
    for i, (source, home_prob, predicted, actual) in enumerate([
        ("skellam", 0.70, "home", "home"), ("negbin", 0.65, "home", "home"), ("heuristic", 0.55, "home", "away"),
    ]):
        session.add(historical_db.HistoricalPrediction(
            run_id=1, game_pk=i, game_date="2024-05-01", season_year=2024, source=source,
            away_prob=1 - home_prob, home_prob=home_prob,
            predicted_winner=predicted, actual_winner=actual, correct=predicted == actual,
        ))
    session.add(historical_db.HistoricalGame(
        run_id=1, game_pk=0, game_date="2024-05-01", season_year=2024,
        away_team="A", home_team="B", winner="home", home_score=5, away_score=2, total_runs=7,
    ))
    session.add(historical_db.HistoricalAnalysis(
        run_id=1, game_pk=0, game_date="2024-05-01", season_year=2024, as_of_date="2024-04-30",
        home_proj_runs=4.5, away_proj_runs=3.0,
    ))
    session.commit()
    session.close()
    return Session


def test_cli_validate_runs_without_error(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch)
    exit_code = cli_main(["validate", "2024", "1"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "skellam" in out


def test_cli_compare_runs_without_error(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch)
    exit_code = cli_main(["compare", "2024", "1"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "historical_confidence_engine" in out


def test_cli_train_runs_without_error(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch)
    exit_code = cli_main(["train", "2024", "1"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Baseline NEGBIN_DISPERSION" in out


def test_cli_report_generates_file(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch)
    output_dir = str(tmp_path / "cli_reports")
    exit_code = cli_main(["report", "2024", "1", "--output-dir", output_dir])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Reporte generado" in out
