from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class PitcherContext:
    """Contexto completo de un pitcher titular"""
    pitcher_id: int
    name: str
    era: float
    k_pct: float
    bb_pct: float
    days_rest: int
    last_outing_pitches: int = 0
    
    # Campos para futuras versiones
    xera: Optional[float] = None
    fip: Optional[float] = None


@dataclass(frozen=True)
class TeamContext:
    """Contexto de un equipo (ofensiva + bullpen)"""
    team_id: int
    name: str
    ops: float
    bullpen_era: float
    
    # Campos para futuras versiones
    wrc_plus: Optional[float] = None
    bullpen_era_last7: Optional[float] = None


@dataclass(frozen=True)
class ParkContext:
    """Información del parque y condiciones ambientales"""
    name: str
    park_factor: float
    temp_f: float
    wind_mph: float = 0.0


@dataclass(frozen=True)
class GameContext:
    """Contexto completo de un partido"""
    game_pk: int
    game_date: date
    away_team: TeamContext
    home_team: TeamContext
    away_pitcher: PitcherContext
    home_pitcher: PitcherContext
    park: ParkContext


@dataclass(frozen=True)
class PredictionResult:
    """Resultado final de la predicción"""
    game_context: GameContext
    away_model_prob: float
    home_model_prob: float
    away_skellam_prob: float
    home_skellam_prob: float
    away_proj_runs: float
    home_proj_runs: float
    fair_total_runs: float
    model_version: str
    git_commit: str
    # Campos opcionales van al final
    away_edge: Optional[float] = None
    home_edge: Optional[float] = None