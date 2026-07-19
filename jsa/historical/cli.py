"""CLI de ingesta historica -- `python -m jsa.historical.cli season 2022`.

Aislado de `jsa/main.py` (produccion en vivo) a proposito -- ver
`tests/test_production_isolation.py`, que ahora tambien verifica que
`main.py` nunca importe nada de `jsa/historical/`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import date

from jsa import config as production_config
from jsa.historical.calibration import fit_and_validate
from jsa.historical.config import HISTORICAL_DATABASE_URL, SUPPORTED_SEASONS
from jsa.historical.discriminative_audit import run_full_audit
from jsa.historical.ingestion_validation import validate_season_ingestion
from jsa.historical.resolution_audit import run_full_resolution_audit
from jsa.historical.trend_candidate_audit import run_full_trend_candidate_audit
from jsa.historical.historical_candidate_audit import run_full_historical_candidate_audit
from jsa.historical.statcast_ingestion import ingest_statcast_season_minimal
from jsa.historical.statcast_candidate_audit import run_full_statcast_candidate_audit
from jsa.historical.game_flow_candidate_audit import run_full_game_flow_candidate_audit
from jsa.historical.merge import merge_databases
from jsa.historical.monte_carlo import run_monte_carlo_audit
from jsa.historical.pillar_contribution import analyze_season_pillar_contribution
from jsa.historical.pipeline import run_season_ingestion
from jsa.historical.validation import benchmark_season
from jsa.registries import db as registries_db

logger = logging.getLogger("jsa.historical")


def setup_logging(season: int) -> str:
    os.makedirs("jsa/logs", exist_ok=True)
    log_file = f"jsa/logs/jsa_historical_{season}_{date.today().strftime('%Y%m%d')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
    )
    return log_file


def setup_plain_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingesta historica de JSA v3.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    season_parser = subparsers.add_parser("season", help="Ingiere una temporada completa")
    season_parser.add_argument("year", type=int, help=f"Temporada a ingerir, una de {SUPPORTED_SEASONS}")
    season_parser.add_argument(
        "--force", action="store_true",
        help="Borra snapshot/report/season_run existentes de la temporada antes de ingerir, forzando un reproceso completo con la logica actual (usar para re-ingestas tras un cambio de reconstruct_snapshot()/evaluate_game(), no para una ingesta inicial)",
    )

    validate_ingestion_parser = subparsers.add_parser(
        "validate-ingestion",
        help="Validaciones estructurales post-ingesta (cobertura de snapshots, cobertura de campos nuevos, consistencia de fechas) -- corre despues de cada temporada re-ingerida, antes de pasar a la siguiente etapa",
    )
    validate_ingestion_parser.add_argument("--db", required=True, help="URL SQLAlchemy de la base historica ya ingerida")
    validate_ingestion_parser.add_argument("--season", type=int, required=True, help="Temporada a validar")
    validate_ingestion_parser.add_argument("--out", help="Si se indica, tambien escribe el resultado como JSON en esta ruta")

    merge_parser = subparsers.add_parser("merge", help="Fusiona N bases historicas separadas en una sola")
    merge_parser.add_argument("--source", action="append", required=True, dest="sources", help="URL SQLAlchemy de una base fuente (repetible, una por temporada)")
    merge_parser.add_argument("--target", required=True, help="URL SQLAlchemy de la base destino fusionada")

    validate_parser = subparsers.add_parser("validate", help="Corre benchmark_season() + Monte Carlo Audit + PillarContributionAnalyzer sobre una base ya ingerida")
    validate_parser.add_argument("--db", required=True, help="URL SQLAlchemy de la base a validar (tipicamente la fusionada)")
    validate_parser.add_argument("--season", action="append", type=int, dest="seasons", required=True, help="Temporada a validar (repetible)")
    validate_parser.add_argument("--monte-carlo-sims", type=int, default=200, help="Cantidad de simulaciones de Monte Carlo Audit por temporada")
    validate_parser.add_argument("--out", help="Si se indica, tambien escribe el resultado como JSON en esta ruta")

    pillar_parser = subparsers.add_parser("pillar-contribution", help="Corre PillarContributionAnalyzer sobre una base ya ingerida (standalone, sin benchmark/Monte Carlo)")
    pillar_parser.add_argument("--db", required=True, help="URL SQLAlchemy de la base a analizar")
    pillar_parser.add_argument("--season", action="append", type=int, dest="seasons", required=True, help="Temporada a analizar (repetible)")
    pillar_parser.add_argument("--out", help="Si se indica, tambien escribe el resultado como JSON en esta ruta")

    calibrate_parser = subparsers.add_parser(
        "calibrate", help="Ajusta y valida (leave-one-season-out) una curva de calibracion isotonica de evidence_score_raw, y la persiste en calibration_registry"
    )
    calibrate_parser.add_argument("--db", required=True, help="URL SQLAlchemy de la base historica ya ingerida (de donde se leen evidence_score_raw + resultados)")
    calibrate_parser.add_argument("--registries-db", help="URL SQLAlchemy de la base de registries -- cae a config.DATABASE_URL si no se pasa (igual que run_season_ingestion)")
    calibrate_parser.add_argument("--season", action="append", type=int, dest="seasons", required=True, help="Temporada a incluir (repetible)")
    calibrate_parser.add_argument("--market", default="moneyline_home", help="Market al que aplica esta curva (default: moneyline_home)")
    calibrate_parser.add_argument("--calibration-id", default="calibration-evidence_score_raw-v1", help="Identificador de esta curva en calibration_registry")
    calibrate_parser.add_argument("--out", help="Si se indica, tambien escribe el resultado como JSON en esta ruta")

    audit_parser = subparsers.add_parser(
        "discriminative-audit",
        help="Audita por que el Evidence Score calibra bien pero discrimina poco -- pilares individuales, correlaciones, ablacion LOSO, optimizacion de pesos, distribucion, separabilidad, curvas y sensibilidad de shrinkage. Solo lectura -- no modifica pillars/, engine/, calibration_registry ni el pipeline.",
    )
    audit_parser.add_argument("--db", required=True, help="URL SQLAlchemy de la base historica ya ingerida")
    audit_parser.add_argument("--season", action="append", type=int, dest="seasons", required=True, help="Temporada a incluir (repetible)")
    audit_parser.add_argument("--optimizer-maxiter", type=int, default=20, help="Iteraciones de differential_evolution para el ajuste de produccion de la Fase 4 (default: 20)")
    audit_parser.add_argument("--optimizer-popsize", type=int, default=10, help="Tamano de poblacion de differential_evolution para el ajuste de produccion de la Fase 4 (default: 10)")
    audit_parser.add_argument("--nested-optimizer-maxiter", type=int, default=10, help="Iteraciones de differential_evolution POR FOLD EXTERNO del nested LOSO de la Fase 4 -- la estimacion sin sesgo de seleccion (default: 10)")
    audit_parser.add_argument("--nested-optimizer-popsize", type=int, default=6, help="Tamano de poblacion de differential_evolution POR FOLD EXTERNO del nested LOSO de la Fase 4 (default: 6)")
    audit_parser.add_argument("--out", help="Si se indica, tambien escribe el resultado como JSON en esta ruta")

    resolution_parser = subparsers.add_parser(
        "resolution-audit",
        help="Sensibilidad de discretizacion (-2..2 vs mas niveles vs percentiles vs z-score vs continuo) para starter/bullpen/offense, y alternativas offline a team_quality (Elo, Pythagorean Expectation). Solo lectura, nunca golpea la API de MLB.",
    )
    resolution_parser.add_argument("--db", required=True, help="URL SQLAlchemy de la base historica ya ingerida")
    resolution_parser.add_argument("--season", action="append", type=int, dest="seasons", required=True, help="Temporada a incluir (repetible)")
    resolution_parser.add_argument("--out", help="Si se indica, tambien escribe el resultado como JSON en esta ruta")

    trend_candidate_parser = subparsers.add_parser(
        "trend-candidate-audit",
        help="Auditoria descriptiva + comparacion LOSO de los 4 candidatos de forma reciente para Trend (rolling OPS/ERA 7d/14d). Solo lectura, nunca golpea la API de MLB, nunca modifica trend.py.",
    )
    trend_candidate_parser.add_argument("--db", required=True, help="URL SQLAlchemy de la base historica ya ingerida")
    trend_candidate_parser.add_argument("--season", action="append", type=int, dest="seasons", required=True, help="Temporada a incluir (repetible)")
    trend_candidate_parser.add_argument("--out", help="Si se indica, tambien escribe el resultado como JSON en esta ruta")

    historical_candidate_parser = subparsers.add_parser(
        "historical-candidate-audit",
        help="Auditoria descriptiva + comparacion LOSO de 4 candidatos de historial head-to-head para Historical Favorite Context (100% offline desde historical_game, nunca golpea la API de MLB, nunca modifica historical.py).",
    )
    historical_candidate_parser.add_argument("--db", required=True, help="URL SQLAlchemy de la base historica ya ingerida")
    historical_candidate_parser.add_argument("--season", action="append", type=int, dest="seasons", required=True, help="Temporada a incluir (repetible)")
    historical_candidate_parser.add_argument("--out", help="Si se indica, tambien escribe el resultado como JSON en esta ruta")

    statcast_ingest_parser = subparsers.add_parser(
        "ingest-statcast",
        help="Ingesta MINIMA de eventos crudos de bateo de Statcast (Etapa 2, solo launch_speed + estimated_woba_using_speedangle, liga completa por ventana de fecha) para una temporada. No toca ningun pilar ni GameSnapshot.",
    )
    statcast_ingest_parser.add_argument("season", type=int, help="Temporada a ingerir")
    statcast_ingest_parser.add_argument("--force", action="store_true", help="Borra los eventos de Statcast ya ingeridos de esta temporada antes de re-ingerir")
    statcast_ingest_parser.add_argument("--chunk-days", type=int, default=30, help="Tamaño de cada ventana de fecha por request HTTP (default: 30)")
    statcast_ingest_parser.add_argument("--out", help="Si se indica, tambien escribe el resumen de costo (tiempo/volumen) como JSON en esta ruta")

    statcast_candidate_parser = subparsers.add_parser(
        "statcast-candidate-audit",
        help="Comparacion LOSO de las 4 hipotesis Statcast (H1-H4) contra el pilar de produccion correspondiente (offense/starter/bullpen/trend). Requiere haber corrido ingest-statcast antes. Solo lectura, nunca modifica ningun pilar.",
    )
    statcast_candidate_parser.add_argument("--db", required=True, help="URL SQLAlchemy de la base historica ya ingerida")
    statcast_candidate_parser.add_argument("--season", action="append", type=int, dest="seasons", required=True, help="Temporada a incluir (repetible)")
    statcast_candidate_parser.add_argument("--out", help="Si se indica, tambien escribe el resultado como JSON en esta ruta")

    game_flow_candidate_parser = subparsers.add_parser(
        "game-flow-candidate-audit",
        help="Comparacion LOSO de las 2 hipotesis del Game Flow Engine v1.0 Etapa 1 (GF1 durabilidad del abridor, GF2 dependencia de bullpen) contra el pilar de produccion correspondiente (starter/bullpen). No requiere ninguna ingesta nueva -- solo lectura, nunca modifica ningun pilar.",
    )
    game_flow_candidate_parser.add_argument("--db", required=True, help="URL SQLAlchemy de la base historica ya ingerida")
    game_flow_candidate_parser.add_argument("--season", action="append", type=int, dest="seasons", required=True, help="Temporada a incluir (repetible)")
    game_flow_candidate_parser.add_argument("--out", help="Si se indica, tambien escribe el resultado como JSON en esta ruta")

    args = parser.parse_args()

    if args.command == "season":
        if args.year not in SUPPORTED_SEASONS:
            parser.error(f"Temporada {args.year} no soportada -- SUPPORTED_SEASONS={SUPPORTED_SEASONS}")
        setup_logging(args.year)
        summary = run_season_ingestion(args.year, force=args.force)
        print(summary)

    elif args.command == "validate-ingestion":
        setup_plain_logging()
        result = validate_season_ingestion(args.db, args.season)
        logger.info(
            "validate-ingestion(%s) completo -- status=%s n_snapshots=%s issues=%s",
            args.season, result["status"], result["n_snapshots"], result["issues"],
        )
        output = json.dumps(result, indent=2, default=str)
        print(output)
        if args.out:
            with open(args.out, "w") as f:
                f.write(output)
        if result["status"] == "failed":
            sys.exit(1)

    elif args.command == "merge":
        setup_plain_logging()
        counts = merge_databases(args.sources, args.target)
        logger.info("merge_databases completo -- target=%s filas_fusionadas=%s", args.target, counts)
        print(counts)

    elif args.command == "validate":
        setup_plain_logging()
        result = {"seasons": {}}
        for season in args.seasons:
            benchmark = benchmark_season(season, args.db)
            audit = run_monte_carlo_audit(season, args.db, n_simulations=args.monte_carlo_sims)
            pillar_contribution = analyze_season_pillar_contribution(season, args.db)
            result["seasons"][season] = {
                "benchmark": benchmark, "monte_carlo_audit": asdict(audit),
                "pillar_contribution": asdict(pillar_contribution),
            }
            logger.info("validate(%s) completo -- n_games_scored=%s", season, benchmark.get("n_games_scored"))
        output = json.dumps(result, indent=2, default=str)
        print(output)
        if args.out:
            with open(args.out, "w") as f:
                f.write(output)

    elif args.command == "pillar-contribution":
        setup_plain_logging()
        result = {"seasons": {}}
        for season in args.seasons:
            report = analyze_season_pillar_contribution(season, args.db)
            result["seasons"][season] = asdict(report)
            logger.info("pillar-contribution(%s) completo -- n_games=%s, most_dominant_pillar=%s", season, report.n_games, report.most_dominant_pillar)
        output = json.dumps(result, indent=2, default=str)
        print(output)
        if args.out:
            with open(args.out, "w") as f:
                f.write(output)

    elif args.command == "calibrate":
        setup_plain_logging()
        result = fit_and_validate(sorted(args.seasons), args.db)

        registries_database_url = args.registries_db or production_config.DATABASE_URL
        engine = registries_db.get_engine(registries_database_url)
        registries_db.init_registries(engine)
        registries_db.append(
            engine, registries_db.calibration_registry,
            calibration_id=args.calibration_id, market=args.market, source_field="evidence_score_raw",
            method="isotonic_regression", x_knots=result["x_knots"], y_knots=result["y_knots"],
            x_min=result["x_min"], x_max=result["x_max"], n_games_fitted=result["n_games_fitted"],
            seasons_used=result["seasons_used"], loso_seasons_validated=result["loso_seasons_validated"],
            loso_n_games=result["loso_n_games"], loso_brier=result["loso_brier"], loso_log_loss=result["loso_log_loss"],
            loso_accuracy=result["loso_accuracy"], loso_ece=result["loso_ece"], loso_mce=result["loso_mce"],
            status=result["status"], date=date.today().isoformat(),
        )
        logger.info(
            "calibrate completo -- calibration_id=%s status=%s loso_seasons=%s loso_brier=%s loso_ece=%s",
            args.calibration_id, result["status"], result["loso_seasons_validated"], result["loso_brier"], result["loso_ece"],
        )

        output = json.dumps(result, indent=2, default=str)
        print(output)
        if args.out:
            with open(args.out, "w") as f:
                f.write(output)

    elif args.command == "discriminative-audit":
        setup_plain_logging()
        result = run_full_audit(
            sorted(args.seasons), args.db,
            optimizer_maxiter=args.optimizer_maxiter, optimizer_popsize=args.optimizer_popsize,
            nested_optimizer_maxiter=args.nested_optimizer_maxiter, nested_optimizer_popsize=args.nested_optimizer_popsize,
        )
        logger.info(
            "discriminative-audit completo -- n_games=%s baseline_brier=%s baseline_ece=%s",
            result.get("n_games"), result.get("baseline", {}).get("loso_brier"), result.get("baseline", {}).get("loso_ece"),
        )
        output = json.dumps(result, indent=2, default=str)
        print(output)
        if args.out:
            with open(args.out, "w") as f:
                f.write(output)

    elif args.command == "resolution-audit":
        setup_plain_logging()
        result = run_full_resolution_audit(sorted(args.seasons), args.db)
        logger.info("resolution-audit completo -- n_games=%s", result.get("n_games"))
        output = json.dumps(result, indent=2, default=str)
        print(output)
        if args.out:
            with open(args.out, "w") as f:
                f.write(output)

    elif args.command == "trend-candidate-audit":
        setup_plain_logging()
        result = run_full_trend_candidate_audit(sorted(args.seasons), args.db)
        logger.info("trend-candidate-audit completo -- n_games=%s", result.get("n_games"))
        output = json.dumps(result, indent=2, default=str)
        print(output)
        if args.out:
            with open(args.out, "w") as f:
                f.write(output)

    elif args.command == "historical-candidate-audit":
        setup_plain_logging()
        result = run_full_historical_candidate_audit(sorted(args.seasons), args.db)
        logger.info("historical-candidate-audit completo -- n_games=%s", result.get("n_games"))
        output = json.dumps(result, indent=2, default=str)
        print(output)
        if args.out:
            with open(args.out, "w") as f:
                f.write(output)

    elif args.command == "ingest-statcast":
        setup_plain_logging()
        summary = ingest_statcast_season_minimal(args.season, HISTORICAL_DATABASE_URL, chunk_days=args.chunk_days, force=args.force)
        output = json.dumps(summary, indent=2, default=str)
        print(output)
        if args.out:
            with open(args.out, "w") as f:
                f.write(output)

    elif args.command == "statcast-candidate-audit":
        setup_plain_logging()
        result = run_full_statcast_candidate_audit(sorted(args.seasons), args.db)
        logger.info("statcast-candidate-audit completo -- n_games=%s", result.get("n_games"))
        output = json.dumps(result, indent=2, default=str)
        print(output)
        if args.out:
            with open(args.out, "w") as f:
                f.write(output)

    elif args.command == "game-flow-candidate-audit":
        setup_plain_logging()
        result = run_full_game_flow_candidate_audit(sorted(args.seasons), args.db)
        logger.info("game-flow-candidate-audit completo -- n_games=%s", result.get("n_games"))
        output = json.dumps(result, indent=2, default=str)
        print(output)
        if args.out:
            with open(args.out, "w") as f:
                f.write(output)


if __name__ == "__main__":
    main()
