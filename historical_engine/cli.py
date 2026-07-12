"""
CLI del motor histórico -- comandos NUEVOS, separados de main.py (el
entrypoint de producción). Nunca se invoca desde main.py ni desde ningún
workflow de producción.

Uso:
    python -m historical_engine.cli season 2024
    python -m historical_engine.cli month 2024 5
    python -m historical_engine.cli date-range 2024-05-01 2024-05-31 2024
    python -m historical_engine.cli team 147 2024
    python -m historical_engine.cli pitcher 543037 2024
    python -m historical_engine.cli game 717468 2024-05-01 2024
    python -m historical_engine.cli validate 2024 <run_id>
    python -m historical_engine.cli compare 2024 <run_id>
    python -m historical_engine.cli train 2024 <run_id>
    python -m historical_engine.cli raw-logs 2024 <run_id>
    python -m historical_engine.cli report 2024 <run_id> [--output-dir DIR]
"""

import argparse
import sys
from datetime import date

from historical_engine import pipeline
from historical_engine.validation import validate_all_sources, compare_seasons_drift
from historical_engine.model_comparison import compare_models
from historical_engine.training import propose_dispersion_recalibration
from historical_engine.reports import generate_historical_report
from historical_engine.raw_ingestion import ingest_raw_logs_for_season


def _print_result(result) -> None:
    print(f"run_id={result.run_id}  juegos={result.n_games}  "
          f"analizados={result.n_analyzed}  saltados_sin_pitcher={result.n_skipped_missing_pitcher}  "
          f"errores={result.n_errors}")
    for e in result.errors[:10]:
        print(f"  - {e}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m historical_engine.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_season = sub.add_parser("season", help="Ingesta + analiza una temporada completa")
    p_season.add_argument("season", type=int)

    p_month = sub.add_parser("month", help="Ingesta + analiza un mes de una temporada")
    p_month.add_argument("season", type=int)
    p_month.add_argument("month", type=int)

    p_range = sub.add_parser("date-range", help="Ingesta + analiza un rango de fechas")
    p_range.add_argument("start", type=str, help="YYYY-MM-DD")
    p_range.add_argument("end", type=str, help="YYYY-MM-DD")
    p_range.add_argument("season", type=int)

    p_team = sub.add_parser("team", help="Ingesta + analiza los juegos de un equipo en una temporada")
    p_team.add_argument("team_id", type=int)
    p_team.add_argument("season", type=int)

    p_pitcher = sub.add_parser("pitcher", help="Ingesta + analiza los juegos de un pitcher en una temporada")
    p_pitcher.add_argument("pitcher_id", type=int)
    p_pitcher.add_argument("season", type=int)

    p_game = sub.add_parser("game", help="Ingesta + analiza un solo juego")
    p_game.add_argument("game_pk", type=int)
    p_game.add_argument("game_date", type=str, help="YYYY-MM-DD")
    p_game.add_argument("season", type=int)

    p_validate = sub.add_parser("validate", help="Calcula métricas de validación de una temporada ya ingerida")
    p_validate.add_argument("season", type=int)
    p_validate.add_argument("run_id", type=int)

    p_compare = sub.add_parser("compare", help="Compara los 4 motores (sin elegir ganador)")
    p_compare.add_argument("season", type=int)
    p_compare.add_argument("run_id", type=int)

    p_drift = sub.add_parser("drift", help="Compara accuracy/Brier de un motor entre temporadas")
    p_drift.add_argument("source", choices=["heuristic", "skellam", "negbin"])
    p_drift.add_argument("seasons", type=int, nargs="+")

    p_train = sub.add_parser("train", help="Propone recalibración de NEGBIN_DISPERSION (nunca la aplica)")
    p_train.add_argument("season", type=int)
    p_train.add_argument("run_id", type=int)

    p_raw_logs = sub.add_parser(
        "raw-logs",
        help="Cachea gameLog crudo de bateo/pitcheo + roster activo (una sola vez, reutilizable para siempre)",
    )
    p_raw_logs.add_argument("season", type=int)
    p_raw_logs.add_argument("run_id", type=int)

    p_report = sub.add_parser("report", help="Genera el reporte HTML histórico")
    p_report.add_argument("season", type=int)
    p_report.add_argument("run_id", type=int)
    p_report.add_argument("--output-dir", default="historical_reports")
    p_report.add_argument("--compare-seasons", type=int, nargs="*", default=None)

    args = parser.parse_args(argv)

    if args.command == "season":
        _print_result(pipeline.run_season(args.season))
    elif args.command == "month":
        _print_result(pipeline.run_month(args.season, args.month))
    elif args.command == "date-range":
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
        _print_result(pipeline.run_date_range(start, end, args.season))
    elif args.command == "team":
        _print_result(pipeline.run_team(args.team_id, args.season))
    elif args.command == "pitcher":
        _print_result(pipeline.run_pitcher(args.pitcher_id, args.season))
    elif args.command == "game":
        _print_result(pipeline.run_single_game(args.game_pk, args.game_date, args.season))
    elif args.command == "validate":
        results = validate_all_sources(args.season, args.run_id)
        for source, metrics in results.items():
            print(f"{source}: n={metrics['n_sample']} accuracy={metrics['accuracy']} "
                  f"brier={metrics['brier_score']} ece={metrics['ece']}")
    elif args.command == "compare":
        result = compare_models(args.season, args.run_id)
        for source, m in result["table"].items():
            print(f"{source}: {m}")
        print("\nObservaciones:")
        for o in result["observations"]:
            print(f"  - {o}")
    elif args.command == "drift":
        result = compare_seasons_drift(args.source, args.seasons)
        print(result)
    elif args.command == "train":
        result = propose_dispersion_recalibration(args.season, args.run_id)
        print(f"Baseline NEGBIN_DISPERSION={result['baseline_value']} brier={result['baseline_brier_score']}")
        for p in result["proposals"]:
            print(f"  candidato k={p['param_value']}: brier={p['brier_score']} mejora={p['improved_over_baseline']}")
        print(result["note"])
    elif args.command == "raw-logs":
        result = ingest_raw_logs_for_season(args.season, args.run_id)
        for layer, stats in result.items():
            print(f"{layer}: {stats}")
    elif args.command == "report":
        path = generate_historical_report(
            args.season, args.run_id, args.output_dir,
            other_seasons_for_drift=args.compare_seasons,
        )
        print(f"Reporte generado: {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
