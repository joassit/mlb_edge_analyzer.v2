"""Lado con I/O de `PillarContributionAnalyzer` (jsa/analytics/
pillar_contribution.py) -- lee `historical_report` de una temporada ya
ingerida y le pasa `feature_contribution` de cada reporte al analizador
puro, mismo patron que `validation.py::benchmark_season()` y
`monte_carlo.py::run_monte_carlo_audit()` (funciones puras en un modulo,
un orquestador con I/O en otro).

El mismo `feature_contribution` vive en `storage/database.py::
jsa_reports.payload` para reportes de produccion -- un lector analogo ahi
(`reports_for_date`/`reports_for_date_range` + este mismo
`PillarContributionAnalyzer`) es la extension natural para correr esto
sobre produccion en vivo el dia que haga falta; no se construye todavia
porque no hay ese pedido concreto aun (YAGNI) y el analizador ya quedo
diseñado para no necesitar cambios cuando se agregue."""

from __future__ import annotations

from jsa.analytics.pillar_contribution import PillarContributionAnalyzer, PillarContributionReport
from jsa.domain.models import FeatureContributionEntry
from jsa.historical import db as historical_db


def analyze_season_pillar_contribution(season: int, historical_database_url: str) -> PillarContributionReport:
    engine = historical_db.get_engine(historical_database_url)
    historical_db.init_historical_storage(engine)  # tolera una base historica nueva/vacia
    reports = historical_db.reports_for_season(engine, season)

    games: list[list[FeatureContributionEntry]] = []
    for report_row in reports:
        payload = report_row["payload"]
        entries = [FeatureContributionEntry(**e) for e in payload["feature_contribution"]]
        games.append(entries)

    return PillarContributionAnalyzer().analyze(games)
