"""`historical/grant_readonly_access.py` -- validacion de identificadores
antes de interpolar en el GRANT, y que la ejecucion real corra contra
cualquier engine SQLAlchemy (se prueba contra SQLite real: no tiene
roles, pero valida que el SQL se emite y que un rol inexistente falla
con un error claro, en vez de silencioso)."""

from __future__ import annotations

import pytest

from jsa.historical.grant_readonly_access import _validate_identifier, grant_select


def test_validate_identifier_accepts_normal_names():
    _validate_identifier("historical_model_prediction", label="table")
    _validate_identifier("jsa_v2", label="role")


@pytest.mark.parametrize("bad", ["jsa_v2; DROP TABLE x", "jsa v2", "jsa'v2", "jsa\"v2", "", "2jsa"])
def test_validate_identifier_rejects_unsafe_names(bad):
    with pytest.raises(ValueError):
        _validate_identifier(bad, label="role")


def test_grant_select_rejects_unsafe_table_before_any_query(tmp_path):
    db_url = f"sqlite:///{tmp_path}/jsa_grant_test.db"
    with pytest.raises(ValueError):
        grant_select(db_url, table="x; DROP TABLE historical_game", role="jsa_v2")


def test_grant_select_rejects_unsafe_role_before_any_query(tmp_path):
    db_url = f"sqlite:///{tmp_path}/jsa_grant_test.db"
    with pytest.raises(ValueError):
        grant_select(db_url, table="historical_model_prediction", role="jsa_v2; DROP TABLE historical_game")
