"""Validaciones automaticas post-ingesta -- corren despues de re-ingerir
una temporada (Trend, schema 3.3->3.4) para detectar un problema real
ANTES de pasar a la siguiente etapa (siguiente temporada, o el analisis
LOSO de los candidatos), en vez de descubrirlo recien al analizar los
resultados finales. Solo lectura -- nunca modifica nada."""

from __future__ import annotations

from jsa.historical import db as historical_db

ROLLING_TREND_FIELDS = (
    "home_team_ops_rolling_7d", "away_team_ops_rolling_7d",
    "home_team_ops_rolling_14d", "away_team_ops_rolling_14d",
    "home_team_era_rolling_7d", "away_team_era_rolling_7d",
    "home_team_era_rolling_14d", "away_team_era_rolling_14d",
)

# Umbrales duros -- si se cruzan, la corrida se marca "failed" (el
# workflow debe detenerse antes de la siguiente temporada). Generosos a
# proposito: juegos de los primeros dias de temporada NO tienen ventana
# de 7/14 dias completa todavia (no hay 7 dias de partidos jugados) --
# eso es esperado, no un bug; solo una cobertura sospechosamente BAJA
# (la API rota, el campo nunca se llena) debe frenar el proceso.
MIN_SNAPSHOT_COVERAGE_PCT = 0.90  # snapshots persistidos / juegos con resultado
MIN_FIELD_COVERAGE_PCT = 0.70  # no-nulos / snapshots, por cada campo rolling


def validate_season_ingestion(historical_database_url: str, season: int) -> dict:
    engine = historical_db.get_engine(historical_database_url)
    historical_db.init_historical_storage(engine)

    games = historical_db.games_for_season(engine, season)
    games_with_result = [g for g in games if g.get("winner") is not None]
    snapshots = historical_db.snapshots_for_season(engine, season)
    snapshot_game_dates = {s["game_pk"]: s["game_date"] for s in snapshots}
    game_dates = {g["game_pk"]: g["game_date"] for g in games}

    issues: list[str] = []

    n_games, n_games_with_result, n_snapshots = len(games), len(games_with_result), len(snapshots)
    snapshot_coverage_pct = (n_snapshots / n_games_with_result) if n_games_with_result else 0.0
    if n_games_with_result > 0 and snapshot_coverage_pct < MIN_SNAPSHOT_COVERAGE_PCT:
        issues.append(
            f"cobertura de snapshots {snapshot_coverage_pct:.1%} por debajo del minimo {MIN_SNAPSHOT_COVERAGE_PCT:.0%} "
            f"({n_snapshots}/{n_games_with_result} juegos con resultado)"
        )

    # Consistencia temporal minima verificable post-hoc: el snapshot de un
    # juego debe corresponder EXACTAMENTE a la fecha de ese juego -- si no
    # coincide, algo referencio el snapshot de otro juego (bug real, no
    # cosmetico: point-in-time depende de que game_date == as_of_date).
    mismatched_dates = [
        pk for pk, snap_date in snapshot_game_dates.items()
        if pk in game_dates and str(snap_date) != str(game_dates[pk])
    ]
    if mismatched_dates:
        issues.append(f"{len(mismatched_dates)} snapshot(s) con game_date distinto al juego que dicen representar: {mismatched_dates[:10]}")

    field_coverage: dict[str, float] = {}
    if snapshots:
        for field in ROLLING_TREND_FIELDS:
            non_null = sum(1 for s in snapshots if s["payload"].get(field) is not None)
            pct = non_null / len(snapshots)
            field_coverage[field] = pct
            if pct < MIN_FIELD_COVERAGE_PCT:
                issues.append(f"cobertura de '{field}' {pct:.1%} por debajo del minimo {MIN_FIELD_COVERAGE_PCT:.0%}")

    return {
        "season": season,
        "n_games": n_games,
        "n_games_with_result": n_games_with_result,
        "n_snapshots": n_snapshots,
        "snapshot_coverage_pct": snapshot_coverage_pct,
        "rolling_trend_field_coverage_pct": field_coverage,
        "issues": issues,
        "status": "failed" if issues else "ok",
    }
