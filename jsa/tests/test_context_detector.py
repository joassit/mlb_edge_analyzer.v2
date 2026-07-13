import datetime

from jsa.domain.models import build_game_snapshot
from jsa.engine.context_detector import detect_context


def _snap(**overrides):
    fields = dict(game_id="g1", game_date=datetime.date(2026, 7, 13), season=2026, home_team="NYY", away_team="BOS")
    fields.update(overrides)
    return build_game_snapshot(**fields)


def test_long_outing_detected():
    ctx = detect_context(_snap(home_starter_projected_ip=6.8, away_starter_projected_ip=6.6))
    assert ctx.long_outing is True
    assert ctx.short_outing_bullpen_game is False


def test_short_outing_detected():
    ctx = detect_context(_snap(home_starter_projected_ip=4.5, away_starter_projected_ip=4.8))
    assert ctx.short_outing_bullpen_game is True


def test_key_offensive_injuries_threshold():
    ctx = detect_context(_snap(home_key_injuries=["a"], away_key_injuries=["b"]))
    assert ctx.key_offensive_injuries is True
    ctx_below = detect_context(_snap(home_key_injuries=["a"], away_key_injuries=[]))
    assert ctx_below.key_offensive_injuries is False


def test_extreme_weather_cold_hot_wind():
    assert detect_context(_snap(weather_temp_f=30)).extreme_weather is True
    assert detect_context(_snap(weather_temp_f=100)).extreme_weather is True
    assert detect_context(_snap(weather_wind_speed=25)).extreme_weather is True
    assert detect_context(_snap(weather_temp_f=70, weather_wind_speed=5)).extreme_weather is False


def test_context_detector_never_produces_weights_or_advantages():
    """Regla estricta de la Seccion 5: el Context Detector solo produce
    hechos -- el modelo ContextSignals no tiene ningun campo de peso ni
    advantage por construccion (verificado via introspection)."""
    ctx = detect_context(_snap())
    field_names = set(type(ctx).model_fields.keys())
    assert not any("weight" in f or "advantage" in f for f in field_names)


def test_small_sample_offense():
    ctx = detect_context(_snap(home_ops_pa_sample=30, away_ops_pa_sample=300))
    assert ctx.small_sample_offense is True
