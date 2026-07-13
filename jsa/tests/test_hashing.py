import datetime

from jsa.domain.hashing import hash_value
from jsa.domain.models import build_game_snapshot


def _snap(**overrides):
    fields = dict(game_id="g1", game_date=datetime.date(2026, 7, 13), season=2026, home_team="NYY", away_team="BOS")
    fields.update(overrides)
    return build_game_snapshot(**fields)


def test_hash_is_deterministic():
    a = _snap()
    b = _snap()
    assert a.snapshot_hash == b.snapshot_hash


def test_hash_changes_with_any_field():
    a = _snap()
    b = _snap(home_team="LAD")
    assert a.snapshot_hash != b.snapshot_hash


def test_hash_independent_of_dict_key_order():
    assert hash_value({"a": 1, "b": 2}) == hash_value({"b": 2, "a": 1})


def test_snapshot_recompute_matches_stored_hash():
    snap = _snap()
    assert snap.compute_hash() == snap.snapshot_hash


def test_mutating_snapshot_invalidates_hash():
    snap = _snap()
    mutated = snap.model_copy(update={"home_team": "BOS"})
    assert mutated.compute_hash() != mutated.snapshot_hash
