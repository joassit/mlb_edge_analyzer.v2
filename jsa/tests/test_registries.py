import time

from jsa.registries import db, seed


def _fresh_engine():
    return db.get_engine("sqlite:///:memory:")


def test_seed_all_populates_expected_counts():
    engine = _fresh_engine()
    seed.seed_all(engine)
    assert len(db.all_rows(engine, db.feature_registry)) == len(seed.FEATURE_SEEDS)
    assert len(db.all_rows(engine, db.rule_registry)) == 6
    assert len(db.all_rows(engine, db.pillar_registry)) == 7
    assert len(db.all_rows(engine, db.market_registry)) == 4
    assert len(db.all_rows(engine, db.schema_migration_registry)) == 2
    assert len(db.all_rows(engine, db.gate_registry)) == 4


def test_seed_all_is_idempotent():
    engine = _fresh_engine()
    seed.seed_all(engine)
    seed.seed_all(engine)
    seed.seed_all(engine)
    assert len(db.all_rows(engine, db.rule_registry)) == 6


def test_all_base_rules_start_experimental():
    engine = _fresh_engine()
    seed.seed_all(engine)
    rules = db.latest_by_id(engine, db.rule_registry, "rule_id")
    assert all(r["status"] == "experimental" for r in rules.values())
    assert all(r["experiments_supporting_rule"] == [] for r in rules.values())


def test_all_seven_base_pillars_active():
    engine = _fresh_engine()
    seed.seed_all(engine)
    pillars = db.latest_by_id(engine, db.pillar_registry, "pillar_id")
    from jsa.domain.models import SEVEN_PILLARS

    for p in SEVEN_PILLARS:
        assert pillars[p]["status"] == "active"


def test_append_only_latest_by_id_returns_newest_row():
    """Un cambio de estado se modela como fila NUEVA, nunca un UPDATE --
    `latest_by_id` debe devolver la mas reciente por `recorded_at`."""
    engine = _fresh_engine()
    db.init_registries(engine)
    db.append(engine, db.market_registry, market_id="totals", description="v1", data_requirements=[], status="experimental", date_added="2026-01-01")
    time.sleep(0.01)
    db.append(engine, db.market_registry, market_id="totals", description="v1", data_requirements=[], status="active", date_added="2026-06-01")

    all_versions = db.all_rows(engine, db.market_registry)
    assert len(all_versions) == 2  # ambas filas siguen existiendo -- nunca se borra

    latest = db.latest_by_id(engine, db.market_registry, "market_id")
    assert latest["totals"]["status"] == "active"
