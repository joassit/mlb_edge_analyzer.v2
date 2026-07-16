"""Siembra v1 de los registries -- llamada UNA vez por corrida desde
`main.py`, idempotente (si un `*_id` ya tiene una fila, no se vuelve a
insertar: "append-only" gobierna los CAMBIOS de estado, no la repeticion
sin cambios de la siembra inicial en cada corrida diaria)."""

from __future__ import annotations

from datetime import date

from sqlalchemy.engine import Engine

from jsa import config
from jsa.domain.models import SCHEMA_VERSION
from jsa.engine.pillars.base import PILLAR_CONTRACT_VERSIONS
from jsa.engine.rule_definitions import RULE_SPECS
from jsa.registries import db

TODAY = date.today().isoformat()

# Version simplificada de "la version del registry completo" para el
# RunManifest (Seccion 14.1: feature_registry_version, rule_registry_version,
# gate_registry_version, market_registry_version). Bootstrap de una sola
# generacion de siembra -- cuando el registry empiece a tener promociones
# reales (experimental -> active), esto debe pasar a ser un hash real del
# contenido del registry en ese momento, no una constante fija.
REGISTRY_VERSION = "v1-bootstrap"

FEATURE_SEEDS: list[dict] = [
    {
        "feature_id": "starter_era_shrunk",
        "name": "home_starter_xera / away_starter_xera (proxy ERA, encogido a liga)",
        "description": "ERA de temporada del abridor, con shrinkage bayesiano hacia el promedio de liga por muestra chica.",
        "source": "MLB Stats API (people/{id}/stats)",
        "transformation": "shrunk_era(era, ip, league_era, k_ip=60.0)",
        "shrinkage_method": "bayesian shrinkage, k_ip=60.0 (literatura sabermetrica estandar, sin calibrar con historial propio)",
        "lineage": {
            "source": "MLB Stats API, people/{id}/stats?stats=season&group=pitching",
            "transformation": "engine.pillars.base.shrunk_era",
            "shrinkage": "bayesian shrinkage k_ip=60.0",
            "feature_store_ref": "GameSnapshot.home_starter_xera / away_starter_xera",
            "engine_ref": "engine.pillars.starter@1.0.0",
            "report_field": "JSAReport.pillar_advantages[starter].explanation",
        },
        "owner": "jsa-bootstrap",
        "date_added": TODAY,
        "version": SCHEMA_VERSION,
        "dependencies": [],
    },
    {
        "feature_id": "offense_ops_factor",
        "name": "home_ops / away_ops (factor no lineal vs. liga)",
        "description": "OPS de equipo, convertido a factor ofensivo no lineal relativo al promedio de liga.",
        "source": "MLB Stats API (teams/{id}/stats)",
        "transformation": "offense_factor(team_ops, league_ops, exponent=1.8)",
        "shrinkage_method": None,
        "lineage": {
            "source": "MLB Stats API, teams/{id}/stats?stats=season&group=hitting",
            "transformation": "engine.pillars.base.offense_factor",
            "shrinkage": None,
            "feature_store_ref": "GameSnapshot.home_ops / away_ops",
            "engine_ref": "engine.pillars.offense@1.0.0",
            "report_field": "JSAReport.pillar_advantages[offense].explanation",
        },
        "owner": "jsa-bootstrap",
        "date_added": TODAY,
        "version": SCHEMA_VERSION,
        "dependencies": [],
    },
    {
        "feature_id": "bullpen_era_weighted",
        "name": "home_bullpen_era / away_bullpen_era",
        "description": "ERA de bullpen ponderado por IP de todos los relevistas del roster activo.",
        "source": "MLB Stats API (teams/{id}/roster + people/{id}/stats por relevista)",
        "transformation": "promedio ponderado por IP entre relevistas (starts==0 o <50% de sus apariciones)",
        "shrinkage_method": None,
        "lineage": {
            "source": "MLB Stats API, teams/{id}/roster + people/{id}/stats",
            "transformation": "data_sources.stats.get_bullpen_era",
            "shrinkage": None,
            "feature_store_ref": "GameSnapshot.home_bullpen_era / away_bullpen_era",
            "engine_ref": "engine.pillars.bullpen@1.0.0",
            "report_field": "JSAReport.pillar_advantages[bullpen].explanation",
        },
        "owner": "jsa-bootstrap",
        "date_added": TODAY,
        "version": SCHEMA_VERSION,
        "dependencies": [],
    },
]

MARKET_SEEDS: list[dict] = [
    {
        "market_id": "moneyline_home",
        "description": "Gana el equipo local.",
        "data_requirements": ["evidence_score_raw", "calibration.calibrated_probability"],
    },
    {
        "market_id": "moneyline_away",
        "description": "Gana el equipo visitante.",
        "data_requirements": ["evidence_score_raw", "calibration.calibrated_probability"],
    },
    {
        "market_id": "run_line",
        "description": "Cubre el hándicap de carreras (+-1.5 tipico).",
        "data_requirements": ["projected_runs.projected_margin", "projected_runs.sigma_margin"],
    },
    {
        "market_id": "totals",
        "description": "Total de carreras del juego, sobre/bajo una linea.",
        "data_requirements": ["projected_runs.mu_home", "projected_runs.mu_away"],
    },
]


def seed_all(engine: Engine) -> None:
    db.init_registries(engine)
    _seed_features(engine)
    _seed_rules(engine)
    _seed_pillars(engine)
    _seed_markets(engine)
    _seed_schema_migration(engine)
    _seed_gates(engine)


def _seed_features(engine: Engine) -> None:
    existing = db.latest_by_id(engine, db.feature_registry, "feature_id")
    for f in FEATURE_SEEDS:
        if f["feature_id"] in existing:
            continue
        db.append(
            engine, db.feature_registry,
            feature_id=f["feature_id"], name=f["name"], description=f["description"], source=f["source"],
            transformation=f["transformation"], real_correlation=None, model_importance=None, divergence_flag=False,
            shrinkage_method=f["shrinkage_method"], lineage=f["lineage"], owner=f["owner"], date_added=f["date_added"],
            status="experimental", version=f["version"], validation_experiment=None, dependencies=f["dependencies"],
        )


def _seed_rules(engine: Engine) -> None:
    existing = db.latest_by_id(engine, db.rule_registry, "rule_id")
    for spec in RULE_SPECS:
        if spec.rule_id in existing:
            continue
        db.append(
            engine, db.rule_registry,
            rule_id=spec.rule_id, trigger=spec.trigger_signal, condition=spec.condition,
            weight_adjustments=spec.weight_adjustments, scientific_justification=spec.scientific_justification,
            version=SCHEMA_VERSION, status="experimental", experiments_supporting_rule=[], trace_link=None,
        )


def _seed_pillars(engine: Engine) -> None:
    existing = db.latest_by_id(engine, db.pillar_registry, "pillar_id")
    for pillar_id, contract_version in PILLAR_CONTRACT_VERSIONS.items():
        if pillar_id in existing:
            continue
        db.append(
            engine, db.pillar_registry,
            pillar_id=pillar_id, status="active", contract_version=contract_version,
            validation_experiment=None, date_added=TODAY,
        )


def _seed_markets(engine: Engine) -> None:
    existing = db.latest_by_id(engine, db.market_registry, "market_id")
    for m in MARKET_SEEDS:
        if m["market_id"] in existing:
            continue
        db.append(
            engine, db.market_registry,
            market_id=m["market_id"], description=m["description"], data_requirements=m["data_requirements"],
            status="active", date_added=TODAY,
        )


def _seed_schema_migration(engine: Engine) -> None:
    existing = db.latest_by_id(engine, db.schema_migration_registry, "migration_id")

    if "schema-3.0-to-3.1" not in existing:
        db.append(
            engine, db.schema_migration_registry,
            migration_id="schema-3.0-to-3.1", from_version="3.0", to_version="3.1", change_type="additive",
            migration_function="identity -- campos nuevos son Optional, snapshots 3.0 los leen como None",
            affected_fields=["league_avg_era", "league_avg_ops", "league_avg_runs_per_game"],
            date=TODAY, backward_read_compatible=True,
        )

    if "schema-3.1-to-3.2" not in existing:
        db.append(
            engine, db.schema_migration_registry,
            migration_id="schema-3.1-to-3.2", from_version="3.1", to_version="3.2", change_type="additive",
            migration_function="identity -- campos nuevos son Optional, snapshots 3.1 los leen como None",
            affected_fields=["home_fielding_pct", "away_fielding_pct"],
            date=TODAY, backward_read_compatible=True,
        )

    if "schema-3.2-to-3.3" not in existing:
        db.append(
            engine, db.schema_migration_registry,
            migration_id="schema-3.2-to-3.3", from_version="3.2", to_version="3.3", change_type="additive",
            migration_function="identity -- campos nuevos son Optional, snapshots 3.2 los leen como None",
            affected_fields=["home_bullpen_ip_sample", "away_bullpen_ip_sample"],
            date=TODAY, backward_read_compatible=True,
        )


def _seed_gates(engine: Engine) -> None:
    existing = db.latest_by_id(engine, db.gate_registry, "gate_id")
    for market_id in config.MARKET_IDS:
        gate_id = f"gate-{market_id}-v1"
        if gate_id in existing:
            continue
        db.append(
            engine, db.gate_registry,
            gate_id=gate_id, market=market_id, p_min=config.GATE_P_MIN, cri_min=config.GATE_CRI_MIN,
            uncertainty_max=config.GATE_UNCERTAINTY_MAX, accuracy_wilson_ci_low=None, accuracy_wilson_ci_high=None,
            coverage_pct=None, coverage_n=None, status="under_validation", validation_seasons=[], manifest_hash=None,
        )


def seed_example_experimental_pillar(engine: Engine) -> None:
    """Solo para `tests/test_pillar_extensibility.py` -- demuestra el
    mecanismo de la Seccion 7.3 con un pilar ficticio que NUNCA se usa en
    produccion (no aparece en `engine.pillars.evaluate_all_pillars`)."""
    existing = db.latest_by_id(engine, db.pillar_registry, "pillar_id")
    if "clutch_index" in existing:
        return
    db.append(
        engine, db.pillar_registry,
        pillar_id="clutch_index", status="experimental", contract_version="clutch_index@0.1.0-experimental",
        validation_experiment=None, date_added=TODAY,
    )
