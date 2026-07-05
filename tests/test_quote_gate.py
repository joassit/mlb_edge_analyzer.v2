"""
Pruebas de data/quote_gate.py -- el gate de validez de tipo/rango y
frescura de una cuota cruda de The Odds API.
"""

from datetime import datetime, timezone, timedelta

from data.quote_gate import gate_quote, GatedQuote


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def test_gate_quote_accepts_valid_fresh_price():
    now = datetime.now(timezone.utc)
    price = {"away_price": -135, "home_price": 115, "last_update": _iso(now - timedelta(minutes=1)), "book": "dk"}

    gated = gate_quote(price, now=now)

    assert isinstance(gated, GatedQuote)
    assert gated.fresh is True
    assert gated.away_price == -135
    assert gated.home_price == 115


def test_gate_quote_marks_old_last_update_as_stale():
    now = datetime.now(timezone.utc)
    price = {"away_price": -135, "home_price": 115, "last_update": _iso(now - timedelta(minutes=30))}

    gated = gate_quote(price, now=now)

    assert gated is not None
    assert gated.fresh is False


def test_gate_quote_rejects_non_numeric_price():
    price = {"away_price": "oops", "home_price": 115, "last_update": None}
    assert gate_quote(price) is None


def test_gate_quote_rejects_odds_out_of_plausible_range():
    price = {"away_price": 50000, "home_price": 115, "last_update": None}
    assert gate_quote(price) is None


def test_gate_quote_rejects_odds_below_american_minimum_magnitude():
    # En formato americano no existe una cuota con |odds| < 100.
    price = {"away_price": 50, "home_price": 115, "last_update": None}
    assert gate_quote(price) is None


def test_gate_quote_treats_missing_last_update_as_fresh():
    price = {"away_price": -135, "home_price": 115}
    gated = gate_quote(price)
    assert gated is not None
    assert gated.fresh is True
    assert gated.age is None


def test_gate_quote_treats_unparseable_last_update_as_fresh():
    price = {"away_price": -135, "home_price": 115, "last_update": "not-a-date"}
    gated = gate_quote(price)
    assert gated is not None
    assert gated.fresh is True
