"""Fase 6 (Seccion 10.3/10.4) -- Gate Threshold Sweep real sobre las 5
temporadas ya ingeridas. Encuentra, para `moneyline_home`/`moneyline_away`
(los UNICOS 2 mercados con una probabilidad calibrada real detras --
`run_line`/`totals` no tienen modelo propio todavia: `evidence_score_raw`/
la curva isotonica solo predicen P(home gana), nunca margen ni total de
carreras. Barrer thresholds para esos 2 mercados seria validar contra una
senal que no les corresponde -- quedan documentados como gap real, no se
les inventa un gate), la combinacion de `(p_min, cri_min, uncertainty_max)`
que alcanza >=70% de accuracy con el limite inferior del intervalo de
Wilson.

Validado con NESTED walk-forward -- mismo criterio anti-sesgo-de-seleccion
que `discriminative_audit.py::optimize_weights_nested()`: por cada
temporada externa, el threshold se elige usando SOLO las 4 temporadas
internas (con su propia curva de calibracion ajustada UNICAMENTE sobre
esas 4), y se evalua en la externa -- nunca el mismo dato usado para
elegir Y para validar. Sin este anidamiento, barrer un grid grande y
quedarse con el mejor combo sobreajustaria, exactamente el mismo riesgo
que ya se evito en la optimizacion de pesos.

Nunca lee el campo `calibration` desde `historical_report.payload` ya
persistido -- esos reportes se generaron ANTES de que Fase 4 wireara la
calibracion real, quedaron congelados en `calibration_status=
"uncalibrated"`. Este modulo refit su propia curva isotonica sobre
`evidence_score_raw` (reusando `calibration._fit_isotonic()`), igual que
`discriminative_audit.py`.

Honestidad de heuristico: `MIN_COVERAGE_N=30` (muestra minima para que un
combo del grid se considere, tanto en la busqueda interna como en la
validacion externa) es un umbral de partida sin calibrar contra el
proyecto -- no existe todavia un criterio propio de "cuantos juegos hacen
falta para confiar en un CI de Wilson" mas alla de la practica
estadistica general (regla informal de n*p>=5 y n*(1-p)>=5 para Wilson,
aca aplicada de forma conservadora)."""

from __future__ import annotations

import math

from jsa.historical import calibration
from jsa.historical import db as historical_db

MARKETS_WITH_MODEL: tuple[str, ...] = ("moneyline_home", "moneyline_away")
MARKETS_WITHOUT_MODEL: tuple[str, ...] = ("run_line", "totals")

P_MIN_GRID: tuple[float, ...] = (0.55, 0.60, 0.65, 0.70, 0.75)
CRI_MIN_GRID: tuple[int, ...] = (70, 75, 80, 85, 90)
UNCERTAINTY_MAX_GRID: tuple[int, ...] = (20, 30, 40, 50)
MIN_COVERAGE_N = 30
MIN_SEASONS_FOR_WALK_FORWARD = 3  # Seccion 10.4, mismo minimo que calibration.py
ACCURACY_VALIDATED_THRESHOLD = 0.70


def load_game_gate_data(engine, seasons: list[int]) -> list[dict]:
    """Un registro por juego con `evidence_score_raw`/`cri_score`/
    `uncertainty_index` -- los 3 insumos que el Gate necesita, leidos de
    `historical_report.payload` (nunca recalculados)."""
    records: list[dict] = []
    for season in seasons:
        games = {g["game_pk"]: g for g in historical_db.games_for_season(engine, season)}
        for report_row in historical_db.reports_for_season(engine, season):
            game = games.get(report_row["game_pk"])
            if game is None or game.get("winner") is None:
                continue
            payload = report_row["payload"]
            evidence_score_raw = payload.get("evidence_score_raw")
            cri_score = payload.get("cri_score")
            uncertainty_index = payload.get("uncertainty_index")
            if evidence_score_raw is None or cri_score is None or uncertainty_index is None:
                continue
            records.append({
                "season": season,
                "game_pk": report_row["game_pk"],
                "home_win": 1 if game["winner"] == "home" else 0,
                "evidence_score_raw": float(evidence_score_raw),
                "cri_score": float(cri_score),
                "uncertainty_index": float(uncertainty_index),
            })
    return records


def _wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de Wilson al 95% -- mas confiable que el normal-aproximado
    con muestras chicas (exactamente el caso de juegos que pasan un gate
    estricto)."""
    if n == 0:
        return (0.0, 0.0)
    phat = successes / n
    denom = 1 + z * z / n
    center = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return (max(0.0, (center - margin) / denom), min(1.0, (center + margin) / denom))


def _market_probability_and_correctness(market_id: str, calibrated_home_prob: float, home_win: int) -> tuple[float, bool]:
    """`moneyline_away` invierte la probabilidad (P(away gana) = 1 -
    P(home gana)) -- misma curva, mercado distinto."""
    if market_id == "moneyline_home":
        return calibrated_home_prob, bool(home_win == 1)
    return 1.0 - calibrated_home_prob, bool(home_win == 0)


def _fit_market_rows(records: list[dict], market_id: str, calibrated_home_probs: list[float]) -> list[dict]:
    rows = []
    for r, calibrated in zip(records, calibrated_home_probs):
        probability, correct = _market_probability_and_correctness(market_id, calibrated, r["home_win"])
        rows.append({
            "probability": probability, "cri_score": r["cri_score"], "uncertainty_index": r["uncertainty_index"],
            "correct": correct,
        })
    return rows


def _best_threshold_combo(rows: list[dict]) -> dict | None:
    """Barre el grid y devuelve el combo con mejor LIMITE INFERIOR de
    Wilson (nunca el de mayor accuracy cruda -- eso favoreceria un combo
    con muestra chica y suerte) entre los que alcanzan `MIN_COVERAGE_N`."""
    best = None
    for p_min in P_MIN_GRID:
        for cri_min in CRI_MIN_GRID:
            for uncertainty_max in UNCERTAINTY_MAX_GRID:
                passing = [
                    r for r in rows
                    if r["probability"] > p_min and r["cri_score"] >= cri_min and r["uncertainty_index"] <= uncertainty_max
                ]
                n = len(passing)
                if n < MIN_COVERAGE_N:
                    continue
                successes = sum(1 for r in passing if r["correct"])
                ci_low, ci_high = _wilson_ci(successes, n)
                if best is None or ci_low > best["ci_low"]:
                    best = {
                        "p_min": p_min, "cri_min": cri_min, "uncertainty_max": uncertainty_max,
                        "ci_low": ci_low, "ci_high": ci_high, "n": n, "successes": successes,
                    }
    return best


def evaluate_gate_threshold_sweep(records: list[dict]) -> dict:
    """Nested walk-forward real -- por cada mercado con modelo, por cada
    temporada externa: el threshold se elige SOLO con las 4 internas (su
    propia curva isotonica, ajustada UNICAMENTE sobre esas 4), y se evalua
    en la externa. Ademas ajusta thresholds de PRODUCCION sobre TODA la
    muestra (igual que `calibration.py`: la curva de produccion usa todos
    los datos disponibles; el nested walk-forward valida el
    PROCEDIMIENTO de seleccion, no literalmente esos thresholds)."""
    seasons_present = sorted({r["season"] for r in records})
    results: dict[str, dict] = {}

    for market_id in MARKETS_WITH_MODEL:
        outer_rows: list[dict] = []
        per_season_combo: dict[int, dict] = {}
        seasons_validated: list[int] = []

        for held_out in seasons_present:
            inner = [r for r in records if r["season"] != held_out]
            outer = [r for r in records if r["season"] == held_out]
            if not inner or not outer:
                continue

            inner_pairs = [(r["evidence_score_raw"], r["home_win"]) for r in inner]
            model = calibration._fit_isotonic(inner_pairs)
            inner_calibrated = model.predict([r["evidence_score_raw"] for r in inner]).tolist()
            outer_calibrated = model.predict([r["evidence_score_raw"] for r in outer]).tolist()

            inner_rows = _fit_market_rows(inner, market_id, inner_calibrated)
            combo = _best_threshold_combo(inner_rows)
            if combo is None:
                continue  # ninguna combinacion del grid alcanzo MIN_COVERAGE_N en las 4 internas

            outer_rows_market = _fit_market_rows(outer, market_id, outer_calibrated)
            passing_outer = [
                r for r in outer_rows_market
                if r["probability"] > combo["p_min"] and r["cri_score"] >= combo["cri_min"]
                and r["uncertainty_index"] <= combo["uncertainty_max"]
            ]
            per_season_combo[held_out] = {
                "p_min": combo["p_min"], "cri_min": combo["cri_min"], "uncertainty_max": combo["uncertainty_max"],
                "n_passing_outer": len(passing_outer),
            }
            seasons_validated.append(held_out)
            outer_rows.extend(passing_outer)

        n_validated = len(outer_rows)
        successes_validated = sum(1 for r in outer_rows if r["correct"])
        ci_low, ci_high = _wilson_ci(successes_validated, n_validated)

        all_pairs = [(r["evidence_score_raw"], r["home_win"]) for r in records]
        production_model = calibration._fit_isotonic(all_pairs)
        production_calibrated = production_model.predict([r["evidence_score_raw"] for r in records]).tolist()
        production_rows = _fit_market_rows(records, market_id, production_calibrated)
        production_combo = _best_threshold_combo(production_rows)

        if len(seasons_validated) >= MIN_SEASONS_FOR_WALK_FORWARD and n_validated >= MIN_COVERAGE_N:
            status = "validated_70" if ci_low >= ACCURACY_VALIDATED_THRESHOLD else "validated_below_70"
        else:
            status = "rejected_insufficient_data"

        results[market_id] = {
            "n_games": len(records),
            "seasons_validated": seasons_validated,
            "per_season_combo": per_season_combo,
            "nested_walk_forward": {
                "n_games_passing_gate": n_validated,
                "n_correct": successes_validated,
                "accuracy": (successes_validated / n_validated) if n_validated else None,
                "accuracy_wilson_ci_low": ci_low,
                "accuracy_wilson_ci_high": ci_high,
                "coverage_pct": (n_validated / len(records) * 100.0) if records else 0.0,
            },
            "production_thresholds": (
                {"p_min": production_combo["p_min"], "cri_min": production_combo["cri_min"], "uncertainty_max": production_combo["uncertainty_max"]}
                if production_combo is not None else None
            ),
            "status": status,
        }

    return {
        "seasons_present": seasons_present,
        "markets_evaluated": list(MARKETS_WITH_MODEL),
        "markets_without_model": list(MARKETS_WITHOUT_MODEL),
        "market_results": results,
    }


def run_gate_threshold_sweep(seasons: list[int], historical_database_url: str) -> dict:
    engine = historical_db.get_engine(historical_database_url)
    historical_db.init_historical_storage(engine)
    records = load_game_gate_data(engine, seasons)
    if not records:
        return {"n_games": 0, "seasons_used": seasons, "error": "no_games_with_full_data"}
    return {"n_games": len(records), "seasons_used": seasons, **evaluate_gate_threshold_sweep(records)}
