"""
Pruebas de db/enums.py -- confirman la garantía central del PASO 3: un
Enum (str, Enum) compara, hashea y persiste igual que el string crudo ya
guardado en filas existentes, sin requerir ninguna migración de datos.
"""

from db.enums import MarketPriceSource, BetResult, PickResult


def test_market_price_source_compares_equal_to_raw_string():
    assert MarketPriceSource.API_LIVE == "api_live"
    assert MarketPriceSource.API_CACHE == "api_cache"
    assert MarketPriceSource.API_STALE_CACHE == "api_stale_cache"
    assert MarketPriceSource.MANUAL == "manual"


def test_bet_result_compares_equal_to_raw_string():
    assert BetResult.PENDING == "pending"
    assert BetResult.WIN == "win"
    assert BetResult.LOSS == "loss"


def test_pick_result_compares_equal_to_raw_string():
    assert PickResult.PENDING == "pending"
    assert PickResult.WIN == "win"
    assert PickResult.LOSS == "loss"
    assert PickResult.PUSH == "push"


def test_enums_hash_identically_to_raw_string_for_dict_lookups():
    # Garantiza que un dict keyeado por Enum (ej. reports/generate_report.py
    # _MARKET_SOURCE_LABELS/_OUTCOME_LABELS) siga resolviendo con una
    # fila vieja que trae el string crudo como clave de búsqueda.
    d = {MarketPriceSource.API_LIVE: "API en vivo"}
    assert d["api_live"] == "API en vivo"

    d2 = {PickResult.WIN: "gana"}
    assert d2["win"] == "gana"


def test_enums_are_str_instances():
    assert isinstance(MarketPriceSource.API_LIVE, str)
    assert isinstance(BetResult.WIN, str)
    assert isinstance(PickResult.PUSH, str)
