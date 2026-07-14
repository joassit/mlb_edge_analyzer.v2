"""`PillarContributionAnalyzer` (jsa/analytics/pillar_contribution.py) --
pura, sin I/O: se testea aqui con `FeatureContributionEntry` sinteticos,
sin tocar ninguna base de datos (esa parte con I/O vive en
jsa/historical/pillar_contribution.py y se testea aparte, igual que
validation.py/monte_carlo.py separan su nucleo puro de su lector)."""

from __future__ import annotations

from jsa.analytics.pillar_contribution import NEGLIGIBLE_CONTRIBUTION_THRESHOLD, PillarContributionAnalyzer
from jsa.domain.models import SEVEN_PILLARS, FeatureContributionEntry


def _entry(pillar: str, advantage: int, absolute_contribution: float, percentage_contribution: float, dominance_warning: bool = False) -> FeatureContributionEntry:
    return FeatureContributionEntry(
        pillar=pillar, final_weight=0.2, advantage=advantage,
        absolute_contribution=absolute_contribution, percentage_contribution=percentage_contribution,
        dominance_warning=dominance_warning,
    )


def _game_with_dominant_pillar(dominant: str, dominant_pct: float = 0.7, dominance_warning: bool = True) -> list[FeatureContributionEntry]:
    remaining = (1.0 - dominant_pct) / (len(SEVEN_PILLARS) - 1)
    entries = []
    for pillar in SEVEN_PILLARS:
        if pillar == dominant:
            entries.append(_entry(pillar, advantage=2, absolute_contribution=0.4, percentage_contribution=dominant_pct, dominance_warning=dominance_warning))
        else:
            entries.append(_entry(pillar, advantage=1, absolute_contribution=0.05, percentage_contribution=remaining))
    return entries


def test_empty_games_returns_empty_report():
    report = PillarContributionAnalyzer().analyze([])
    assert report.n_games == 0
    assert report.stats_by_pillar == {}
    assert report.most_dominant_pillar is None
    assert report.least_contributing_pillar is None


def test_most_dominant_pillar_identified_correctly():
    games = [_game_with_dominant_pillar("starter") for _ in range(5)]
    report = PillarContributionAnalyzer().analyze(games)
    assert report.n_games == 5
    assert report.most_dominant_pillar == "starter"
    assert report.stats_by_pillar["starter"].mean_percentage_contribution > 0.5


def test_top_contributor_rate_reflects_which_pillar_wins_each_game():
    games = [_game_with_dominant_pillar("starter")] * 3 + [_game_with_dominant_pillar("bullpen")] * 2
    report = PillarContributionAnalyzer().analyze(games)
    assert report.stats_by_pillar["starter"].top_contributor_rate == 3 / 5
    assert report.stats_by_pillar["bullpen"].top_contributor_rate == 2 / 5
    assert report.stats_by_pillar["offense"].top_contributor_rate == 0.0


def test_dominance_warning_rate_counts_only_flagged_games():
    games = [
        _game_with_dominant_pillar("starter", dominance_warning=True),
        _game_with_dominant_pillar("starter", dominance_warning=False),
        _game_with_dominant_pillar("starter", dominance_warning=True),
        _game_with_dominant_pillar("starter", dominance_warning=True),
    ]
    report = PillarContributionAnalyzer().analyze(games)
    assert report.stats_by_pillar["starter"].dominance_warning_rate == 3 / 4


def test_zero_advantage_rate_flags_silent_pillar():
    games = []
    for _ in range(4):
        entries = [_entry(p, advantage=1, absolute_contribution=0.1, percentage_contribution=1 / len(SEVEN_PILLARS)) for p in SEVEN_PILLARS]
        entries = [e if e.pillar != "historical" else _entry("historical", advantage=0, absolute_contribution=0.0, percentage_contribution=0.0) for e in entries]
        games.append(entries)
    report = PillarContributionAnalyzer().analyze(games)
    assert report.stats_by_pillar["historical"].zero_advantage_rate == 1.0
    assert report.stats_by_pillar["starter"].zero_advantage_rate == 0.0


def test_negligible_contribution_rate_uses_module_threshold():
    tiny_pct = NEGLIGIBLE_CONTRIBUTION_THRESHOLD / 2
    games = [
        [_entry(p, advantage=1, absolute_contribution=0.01, percentage_contribution=tiny_pct if p == "trend" else 0.2) for p in SEVEN_PILLARS]
        for _ in range(3)
    ]
    report = PillarContributionAnalyzer().analyze(games)
    assert report.stats_by_pillar["trend"].negligible_contribution_rate == 1.0


def test_missing_pillar_entry_in_a_game_defaults_to_zero_not_a_crash():
    partial_game = [_entry("starter", advantage=2, absolute_contribution=0.5, percentage_contribution=1.0)]
    report = PillarContributionAnalyzer().analyze([partial_game])
    assert report.n_games == 1
    assert report.stats_by_pillar["bullpen"].mean_percentage_contribution == 0.0
    assert report.stats_by_pillar["starter"].mean_percentage_contribution == 1.0


def test_least_contributing_pillar_identified_correctly():
    games = [_game_with_dominant_pillar("starter") for _ in range(3)]
    report = PillarContributionAnalyzer().analyze(games)
    # Todos los demas pilares comparten el mismo pct residual -- el "menos
    # contribuyente" debe ser alguno de ellos, nunca el dominante.
    assert report.least_contributing_pillar != "starter"


def test_tied_pillars_report_no_dominant_or_least_contributing():
    # Todos los pilares con exactamente el mismo aporte promedio -- ni
    # most_dominant_pillar ni least_contributing_pillar deben elegir un
    # ganador arbitrario por orden de iteracion (serian contradictorios:
    # el mismo pilar como "el que mas" y "el que menos" aporta).
    neutral_game = [_entry(p, advantage=0, absolute_contribution=0.0, percentage_contribution=0.0) for p in SEVEN_PILLARS]
    report = PillarContributionAnalyzer().analyze([neutral_game, neutral_game])
    assert report.most_dominant_pillar is None
    assert report.least_contributing_pillar is None


def test_all_seven_pillars_always_present_in_stats():
    games = [_game_with_dominant_pillar("offense") for _ in range(2)]
    report = PillarContributionAnalyzer().analyze(games)
    assert set(report.stats_by_pillar.keys()) == set(SEVEN_PILLARS)
