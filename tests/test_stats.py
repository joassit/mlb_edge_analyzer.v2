from data.stats import _parse_innings


def test_parse_innings_with_thirds():
    assert abs(_parse_innings("63.1") - 63.3333) < 0.001
    assert abs(_parse_innings("63.2") - 63.6667) < 0.001


def test_parse_innings_whole_numbers():
    assert _parse_innings("63.0") == 63.0
    assert _parse_innings("10") == 10.0
