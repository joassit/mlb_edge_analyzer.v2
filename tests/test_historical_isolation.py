"""
LA prueba central que pidió el usuario: demostrar, en tiempo de ejecución
(no solo por inspección de código), que el motor histórico NUNCA toca
producción.

Estrategia: se arma una base de datos de PRODUCCIÓN de prueba (con el
esquema real de db/database.py) con un GameAnalysis/Pick/ActualResult ya
cargados -- incluido un escenario que hace que
count_liquidated_picks_with_market_odds() dé un valor conocido (el
"contador de 200 picks" real de producción). Se corre el pipeline
histórico COMPLETO (ingesta simulada + análisis + validación +
comparación + entrenamiento) contra su propia base separada. Al final se
vuelve a leer la base de producción y se compara byte a byte / fila a
fila contra el estado inicial -- cero cambios.
"""

import hashlib

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

import db.database as production_db
import tracking.results_tracker as results_tracker
import historical_engine.db as historical_db
import historical_engine.pipeline as pipeline
from tests.test_historical_point_in_time import FakeProvider


def _seed_production_db(tmp_path):
    """Arma una base de producción de prueba, con datos suficientes para
    que count_liquidated_picks_with_market_odds() (el contador de 200
    picks) dé un valor conocido y no trivial."""
    engine = create_engine(f"sqlite:///{tmp_path}/production_mirror.db")
    production_db.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    session.add(production_db.GameAnalysis(
        game_pk=555, game_date="2026-07-06", away_team="A", home_team="B",
        away_model_prob=0.45, home_model_prob=0.55,
    ))
    session.add(production_db.ActualResult(
        game_pk=555, game_date="2026-07-06", home_score=5, away_score=2, winner="home", total_runs=7,
    ))
    session.add(production_db.Pick(
        game_pk=555, game_date="2026-07-06", market="moneyline", selection="home",
        model_prob=0.55, odds_used=-150, result="win", profit_unit=0.66,
    ))
    session.add(production_db.Pick(
        game_pk=556, game_date="2026-07-06", market="moneyline", selection="away",
        model_prob=0.60, odds_used=130, result="loss", profit_unit=-1.0,
    ))
    session.commit()
    session.close()
    return engine, Session


def _table_row_hash(engine, table_name: str) -> str:
    """Hash determinista de TODAS las filas de una tabla -- cualquier
    escritura, update o borrado cambia este hash, sin importar si el
    conteo de filas se mantiene igual por coincidencia."""
    with engine.connect() as conn:
        from sqlalchemy import text
        rows = conn.execute(text(f"SELECT * FROM {table_name} ORDER BY rowid")).fetchall()
    serialized = "|".join(str(tuple(row)) for row in rows)
    return hashlib.sha256(serialized.encode()).hexdigest()


PRODUCTION_TABLES = ["game_analysis", "actual_results", "picks", "bets", "feature_snapshots"]


def test_historical_pipeline_never_writes_to_production_tables(tmp_path, monkeypatch):
    prod_engine, ProdSession = _seed_production_db(tmp_path)

    # El contador oficial de 200 picks, ANTES de tocar el motor histórico.
    monkeypatch.setattr(results_tracker, "SessionLocal", ProdSession)
    monkeypatch.setattr(production_db, "SessionLocal", ProdSession)
    counter_before = results_tracker.count_liquidated_picks_with_market_odds()
    hashes_before = {t: _table_row_hash(prod_engine, t) for t in PRODUCTION_TABLES}

    # Corre el pipeline histórico COMPLETO contra SU PROPIA base, separada.
    hist_engine = create_engine(f"sqlite:///{tmp_path}/historical_isolation_test.db")
    historical_db.HistoricalBase.metadata.create_all(hist_engine)
    HistSession = sessionmaker(bind=hist_engine)
    monkeypatch.setattr(historical_db, "SessionLocal", HistSession)
    monkeypatch.setattr(pipeline, "SessionLocal", HistSession)

    run_id = 1
    session = HistSession()
    session.add(historical_db.HistoricalRun(id=run_id, run_type="game", scope_description="isolation test", season_year=2024))
    session.add(historical_db.HistoricalGame(
        run_id=run_id, game_pk=9001, game_date="2024-05-01", season_year=2024,
        away_team="X", home_team="Y", away_team_id=111, home_team_id=147,
        away_pitcher_id=2001, home_pitcher_id=2002,
        status="Final", home_score=5, away_score=2, winner="home", total_runs=7,
    ))
    session.commit()
    session.close()

    result = pipeline._run_over_games(run_id, season=2024, provider=FakeProvider())
    assert result.n_analyzed == 1  # confirma que el pipeline histórico SÍ hizo trabajo real

    # El contador oficial de 200 picks, DESPUÉS -- debe ser IDÉNTICO.
    counter_after = results_tracker.count_liquidated_picks_with_market_odds()
    hashes_after = {t: _table_row_hash(prod_engine, t) for t in PRODUCTION_TABLES}

    assert counter_after == counter_before, (
        "El contador oficial de 200 picks cambió después de correr el motor histórico -- "
        "contaminación de producción detectada."
    )
    assert hashes_after == hashes_before, (
        "Al menos una tabla de producción cambió de contenido después de correr el motor histórico."
    )

    # Y en paralelo, el motor histórico sí generó sus propias filas -- no
    # es que "no hizo nada", hizo su trabajo en su propia base.
    hist_session = HistSession()
    n_historical_analyses = hist_session.query(historical_db.HistoricalAnalysis).count()
    n_historical_predictions = hist_session.query(historical_db.HistoricalPrediction).count()
    hist_session.close()
    assert n_historical_analyses == 1
    assert n_historical_predictions == 3


def test_historical_engine_never_imports_production_session_local():
    """Chequeo estático complementario: ningún módulo de historical_engine
    importa db.database.SessionLocal ni db.database.engine directamente --
    si alguno lo hiciera, este test lo detecta leyendo el código fuente."""
    import ast
    import pathlib

    historical_dir = pathlib.Path(__file__).parent.parent / "historical_engine"
    offenders = []
    for py_file in historical_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "db.database":
                offenders.append((py_file.name, [a.name for a in node.names]))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in ("db.database", "db"):
                        offenders.append((py_file.name, alias.name))

    assert offenders == [], f"historical_engine importa db.database directamente: {offenders}"


def test_historical_engine_never_imports_tracking_results_tracker():
    """El motor histórico no debe depender del tracking oficial de
    producción (tracking/results_tracker.py) -- tiene su propio
    validation.py con su propia lógica, deliberadamente separada."""
    import ast
    import pathlib

    historical_dir = pathlib.Path(__file__).parent.parent / "historical_engine"
    offenders = []
    for py_file in historical_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "tracking" in node.module:
                offenders.append(py_file.name)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "tracking" in alias.name:
                        offenders.append(py_file.name)

    assert offenders == [], f"historical_engine importa tracking/: {offenders}"


def test_historical_engine_never_imports_reports_generate_report():
    """El motor histórico tiene su propio reports.py -- nunca debe
    reutilizar ni importar reports/generate_report.py (el reporte oficial)."""
    import ast
    import pathlib

    historical_dir = pathlib.Path(__file__).parent.parent / "historical_engine"
    offenders = []
    for py_file in historical_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "reports.generate_report" in node.module:
                offenders.append(py_file.name)

    assert offenders == [], f"historical_engine importa reports/generate_report.py: {offenders}"


def test_daily_pipeline_workflow_does_not_reference_historical_engine():
    """El workflow de producción (.github/workflows/daily_pipeline.yml)
    nunca debe invocar historical_engine -- confirma que el cron oficial
    sigue corriendo main.py exactamente igual que antes."""
    import pathlib

    workflow_path = pathlib.Path(__file__).parent.parent / ".github" / "workflows" / "daily_pipeline.yml"
    content = workflow_path.read_text()
    assert "historical_engine" not in content
    assert "python main.py" in content


def test_main_py_does_not_import_historical_engine():
    """main.py (el pipeline diario de producción) nunca debe importar
    historical_engine -- confirma que producción no adquirió una
    dependencia nueva hacia el motor de backtesting."""
    import ast
    import pathlib

    main_path = pathlib.Path(__file__).parent.parent / "main.py"
    tree = ast.parse(main_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "historical_engine" in node.module:
            raise AssertionError("main.py importa historical_engine -- producción no debe depender del motor histórico")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "historical_engine" not in alias.name
