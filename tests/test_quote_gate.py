"""
Pruebas de data/quote_gate.py -- el gate de validez de tipo/rango y
frescura de una cuota cruda de The Odds API.
"""

from datetime import datetime, timezone, timedelta

from data.quote_gate import gate_quote, GatedQuote, validate_manual_american_odds, validate_manual_line


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


# --- C2: validate_manual_american_odds / validate_manual_line ---
# MARKET_ODDS/MARKET_SPREADS/MARKET_TOTALS son 100% cuotas cargadas a mano
# en los mercados de Run Line/Totales -- un typo de formato decimal (1.91
# en vez de -110) producía una probabilidad implícita fantasma (~0.98).

def test_validate_manual_american_odds_rejects_decimal_typo():
    assert validate_manual_american_odds(1.91) is None


def test_validate_manual_american_odds_rejects_decimal_typo_as_string():
    assert validate_manual_american_odds("1.91") is None


def test_validate_manual_american_odds_accepts_valid_negative_int():
    assert validate_manual_american_odds(-110) == -110


def test_validate_manual_american_odds_accepts_valid_positive_int():
    assert validate_manual_american_odds(150) == 150


def test_validate_manual_american_odds_accepts_whole_number_float():
    # -110.0 es un float pero SIN parte fraccionaria -- válido.
    assert validate_manual_american_odds(-110.0) == -110


def test_validate_manual_american_odds_rejects_fractional_float():
    # -110.5 no es una cuota americana real (siempre enteras).
    assert validate_manual_american_odds(-110.5) is None


def test_validate_manual_american_odds_rejects_below_minimum_magnitude():
    assert validate_manual_american_odds(50) is None


def test_validate_manual_american_odds_rejects_above_maximum_magnitude():
    assert validate_manual_american_odds(15000) is None


def test_validate_manual_american_odds_rejects_bool():
    # bool es subclase de int en Python -- True/False no son cuotas.
    assert validate_manual_american_odds(True) is None


def test_validate_manual_american_odds_rejects_non_numeric():
    assert validate_manual_american_odds("-110 (a confirmar)") is None
    assert validate_manual_american_odds(None) is None


def test_validate_manual_line_accepts_standard_run_line():
    assert validate_manual_line(1.5) == 1.5


def test_validate_manual_line_accepts_standard_totals_line():
    assert validate_manual_line(8.5) == 8.5


def test_validate_manual_line_rejects_negative():
    assert validate_manual_line(-1.5) is None


def test_validate_manual_line_rejects_zero():
    assert validate_manual_line(0) is None


def test_validate_manual_line_rejects_absurdly_high_value():
    assert validate_manual_line(100.0) is None


def test_validate_manual_line_rejects_non_numeric():
    assert validate_manual_line("8.5 carreras") is None
