from dataclasses import dataclass, field
from typing import Optional
from datetime import date

@dataclass(frozen=True)
class PitcherContext:
    pitcher_id: int
    name: str
    era: float
    k_pct: float
    bb_pct: float
    days_rest: int
    last_outing_pitches: int = 0

@dataclass(frozen=True)
class TeamContext:
    team_id: int
    name: str
    ops: float
    bullpen_era: float

@dataclass(frozen=True)
class ParkContext:
    name: str
    park_factor: float
    lat: float
    lon: float

@dataclass(frozen=True)
class WeatherContext:
    temp_f: float

@dataclass(frozen=True)
class GameContext:
    game_pk: int
    game_date: date
    away_team: TeamContext
    home_team: TeamContext
    away_pitcher: PitcherContext
    home_pitcher: PitcherContext
    park: ParkContext
    weather: WeatherContext