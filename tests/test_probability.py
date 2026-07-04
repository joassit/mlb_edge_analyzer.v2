from model.probability import (
    pitcher_score, hitter_score, model_prob, normalize_matchup,
    command_adjustment, fatigue_adjustment, park_weather_adjustment,
    pitching_staff_score,
)


def test_pitcher_score_low_era_beats_high_era():
    assert pitcher_score(2.0) > pitcher_score(4.0)


def test_pitcher_score_bounds():
    assert pitcher_score(10) == 0
    assert 0 <= pitcher_score(0) <= 1


def test_hitter_score_league_average_is_one():
    assert abs(hitter_score(0.750, league_ops=0.750) - 1.0) < 1e-9


def test_hitter_score_capped_at_1_3():
    assert hitter_score(2.0, league_ops=0.750) == 1.3


def test_model_prob_stays_in_valid_range():
    p = model_prob(3.5, 0.750)
    assert 0.05 <= p <= 0.95


def test_model_prob_backward_compatible_without_new_params():
    p = model_prob(3.0, 0.780, league_ops=0.750)
    assert isinstance(p, float)


def test_normalize_matchup_sums_to_one():
    away, home = normalize_matchup(0.6, 0.5)
    assert abs((away + home) - 1.0) < 1e-9


def test_normalize_matchup_home_field_boosts_home_team():
    _, home_sin = normalize_matchup(0.5, 0.5, home_field_adv=0.0)
    _, home_con = normalize_matchup(0.5, 0.5, home_field_adv=0.02)
    assert home_con > home_sin


def test_command_adjustment_rewards_good_command():
    good = command_adjustment(k_pct=0.30, bb_pct=0.05)
    bad = command_adjustment(k_pct=0.15, bb_pct=0.12)
    assert good > 0 > bad


def test_command_adjustment_none_is_neutral():
    assert command_adjustment(None, None) == 0.0


def test_fatigue_adjustment_penalizes_short_rest_heavy_pitch_count():
    assert fatigue_adjustment(days_rest=3, last_outing_pitches=105) < 0
    assert fatigue_adjustment(days_rest=5, last_outing_pitches=90) == 0.0


def test_park_weather_adjustment_hitter_park_is_positive():
    assert park_weather_adjustment(park_factor=1.15) > 0
    assert park_weather_adjustment(park_factor=0.90) < 0


def test_pitching_staff_score_rewards_good_bullpen():
    good_bullpen = pitching_staff_score(3.0, 2.5)
    bad_bullpen = pitching_staff_score(3.0, 5.0)
    assert good_bullpen > bad_bullpen
