"""Seccion 6.6 + 12.8 del spec JSA v3.0 -- primera evaluacion formal de
las reglas heredadas (`engine/rule_definitions.py::RULE_SPECS`) contra el
historico real de 5 temporadas. Ninguna de las 6 reglas tiene todavia un
`supporting_experiment_id` real (ver `registries/seed.py`,
`engine/rule_engine.py`) -- HOY ninguna se aplica jamas en produccion
(`status="experimental"` las 6), pese a evaluarse y trazarse en cada
corrida (Rule Trace, Seccion 6.6). Este modulo cierra ese vacio: simula,
para cada regla, que pasaria si estuviera activa (redistribuye pesos
SOLO en los juegos donde su trigger realmente dispara, reusando
`engine/weight_engine.py::apply_weights()` -- nunca reimplementa esa
logica), y compara via LOSO + significancia formal
(`jsa/historical/significance.py`, las 3 pruebas: bootstrap + McNemar +
permutacion) contra `evidence_score_raw` real (pesos base, ninguna regla
aplicada -- el estado REAL de produccion hoy).

El trigger de cada regla se evalua reconstruyendo un `GameSnapshot` real
del payload persistido (`GameSnapshot(**snapshot_payload)`, mismo patron
que `validation.py::benchmark_season()`) y llamando a
`engine/context_detector.py::detect_context()` sin modificarlo -- cero
riesgo de que esta auditoria evalue una condicion sutilmente distinta a
la que ya corre en produccion.

5 de las 6 reglas tienen dato real wireado en `historical_snapshot`
(`long_outing`, `short_outing_bullpen_game`, `key_offensive_injuries`,
`double_header`, `extreme_travel` -- confirmado leyendo
`snapshot_reconstruction.py` directamente). `bullpen_fatigue` queda
explicitamente FUERA de este audit: su campo
(`home/away_bullpen_ip_last_3_days`) esta declarado en `domain/models.py`
pero nunca se llena en ningun lado de la ingesta (siempre `None`) --
testearla ahora seria evaluar una regla contra un trigger que nunca
puede disparar, fingiendo evidencia que no existe. Requiere su propia
fuente de datos + re-ingesta antes de poder evaluarse, igual que
cualquier campo nuevo anterior (Trend, fielding%, Statcast)."""

from __future__ import annotations

from jsa.domain.models import SEVEN_PILLARS, GameSnapshot
from jsa.engine.context_detector import detect_context
from jsa.engine.rule_definitions import RULE_SPECS, RuleSpec
from jsa.engine.weight_engine import apply_weights
from jsa.historical import calibration
from jsa.historical import db as historical_db
from jsa.historical.discriminative_audit import load_game_pillar_data
from jsa.historical.significance import full_significance_report

TESTABLE_RULE_IDS: tuple[str, ...] = (
    "long_outing",
    "short_outing_bullpen_game",
    "key_offensive_injuries",
    "double_header",
    "extreme_travel",
)
UNTESTABLE_RULE_IDS: tuple[str, ...] = ("bullpen_fatigue",)

_RULE_SPECS_BY_ID: dict[str, RuleSpec] = {r.rule_id: r for r in RULE_SPECS}


def _trigger_fired(spec: RuleSpec, snapshot_payload: dict) -> bool:
    snapshot = GameSnapshot(**snapshot_payload)
    context = detect_context(snapshot)
    return bool(getattr(context, spec.trigger_signal))


def _alt_score_for_rule(spec: RuleSpec, record: dict, fired: bool) -> float:
    """Si la regla no disparo en este juego, el score no cambia (la regla
    nunca toco este juego). Si disparo, se recalcula con los pesos que
    resultarian de aplicarla EN SOLITARIO (mismo criterio que Game Flow/
    Statcast/Trend: se prueba una hipotesis a la vez, nunca combinaciones
    sin evidencia individual primero)."""
    if not fired:
        return record["evidence_score_raw"]
    deltas = spec.weight_adjustments
    rules_applied_per_pillar = {p: [spec.rule_id] for p in deltas}
    alt_weights, _ = apply_weights(record["weights"], deltas, rules_applied_per_pillar)
    alt_weights_dict = alt_weights.as_dict()
    return sum(alt_weights_dict[p] * record["advantages"][p] for p in SEVEN_PILLARS)


def load_records_with_rule_triggers(engine, seasons: list[int]) -> list[dict]:
    """Extiende `load_game_pillar_data()` (los mismos registros que ya
    usan `resolution_audit.py`/`statcast_candidate_audit.py`/etc.) con,
    para cada regla testeable, si su trigger disparo en ese juego."""
    records = load_game_pillar_data(engine, seasons)
    for r in records:
        snapshot_payload = r["snapshot"]
        r["rule_triggers"] = {
            rule_id: _trigger_fired(_RULE_SPECS_BY_ID[rule_id], snapshot_payload)
            for rule_id in TESTABLE_RULE_IDS
        }
    return records


def evaluate_rule_candidates(records: list[dict]) -> dict:
    """Baseline = `evidence_score_raw` real (pesos base, ninguna regla
    aplicada -- el estado real de produccion hoy). Alternativa, por
    regla = mismo score, recalculado con los pesos que resultarian de
    aplicarla solo en los juegos donde su trigger disparo. LOSO +
    reporte de significancia completo sobre ambos vectores -- nunca se
    declara una regla lista para promocion sin que las 3 pruebas
    coincidan (`full_significance_report()["passes_all_three"]`)."""
    baseline_pairs_by_season: dict[int, list[tuple[float, int]]] = {}
    for r in records:
        baseline_pairs_by_season.setdefault(r["season"], []).append((r["evidence_score_raw"], r["home_win"]))
    baseline_loso = calibration.loso_fit_and_score(baseline_pairs_by_season)

    out = {}
    for rule_id in TESTABLE_RULE_IDS:
        spec = _RULE_SPECS_BY_ID[rule_id]
        n_triggered = sum(1 for r in records if r["rule_triggers"][rule_id])

        alt_pairs_by_season: dict[int, list[tuple[float, int]]] = {}
        for r in records:
            fired = r["rule_triggers"][rule_id]
            score = _alt_score_for_rule(spec, r, fired)
            alt_pairs_by_season.setdefault(r["season"], []).append((score, r["home_win"]))
        alt_loso = calibration.loso_fit_and_score(alt_pairs_by_season)

        significance = full_significance_report(baseline_loso["loso_pairs"], alt_loso["loso_pairs"])

        out[rule_id] = {
            "n_games": len(records),
            "n_triggered": n_triggered,
            "trigger_rate_pct": (n_triggered / len(records) * 100.0) if records else 0.0,
            "weight_adjustments": spec.weight_adjustments,
            "loso_if_rule_active": {
                "loso_brier": alt_loso["loso_brier"],
                "loso_log_loss": alt_loso["loso_log_loss"],
                "loso_accuracy": alt_loso["loso_accuracy"],
                "loso_ece": alt_loso["loso_ece"],
            },
            "current_loso": {
                "loso_brier": baseline_loso["loso_brier"],
                "loso_log_loss": baseline_loso["loso_log_loss"],
                "loso_accuracy": baseline_loso["loso_accuracy"],
            },
            "significance": significance,
        }
    return out


def run_full_rule_candidate_audit(seasons: list[int], historical_database_url: str) -> dict:
    engine = historical_db.get_engine(historical_database_url)
    historical_db.init_historical_storage(engine)
    records = load_records_with_rule_triggers(engine, seasons)
    if not records:
        return {"n_games": 0, "seasons_used": seasons, "error": "no_games_with_full_data"}
    return {
        "n_games": len(records),
        "seasons_used": seasons,
        "untestable_rules": list(UNTESTABLE_RULE_IDS),
        "rule_results": evaluate_rule_candidates(records),
    }
