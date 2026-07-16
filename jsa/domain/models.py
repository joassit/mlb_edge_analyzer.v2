"""Modelos de datos Pydantic v2 -- Seccion 3 del spec JSA v3.0.

Estos modelos son el contrato central del sistema. `GameSnapshot` es
aditivo por diseno (Principio 7): los campos nuevos se agregan como
`Optional` sin romper snapshots anteriores, y cualquier cambio de
significado semantico pasa por el Schema Migration Registry (Seccion 3.3,
ver `registries/db.py`), nunca por editar un campo existente in situ.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jsa.domain.hashing import hash_model_excluding

SCHEMA_VERSION = "3.3"

PillarName = Literal[
    "starter",
    "bullpen",
    "offense",
    "team_quality",
    "context",
    "trend",
    "historical",
]

SEVEN_PILLARS: tuple[PillarName, ...] = (
    "starter",
    "bullpen",
    "offense",
    "team_quality",
    "context",
    "trend",
    "historical",
)

RegistryStatus = Literal["experimental", "active", "deprecated"]
GateStatus = Literal[
    "under_validation", "validated_70", "validated_below_70", "rejected_insufficient_data"
]
InvalidationReason = Literal[
    "unversioned_git_commit",
    "manifest_absent",
    "hash_mismatch",
    "active_feature_without_validation_experiment",
    "active_rule_without_supporting_experiment",
    "divergence_flag_dominant_unreviewed",
    "reliability_diagram_missing",
    "miscalibrated_high_confidence_bucket",
    "gate_bypassed",
    "benchmark_failed",
    "pillar_code_hash_mismatch",
    "experimental_pillar_in_evidence_score",
    "uncalibrated",
]


class GameSnapshot(BaseModel):
    """Nucleo del Feature Store -- Seccion 3.1. Snapshot de punto-en-el-tiempo,
    inmutable una vez persistido: cualquier modificacion posterior invalida
    su `snapshot_hash` y, por lo tanto, toda corrida que lo haya usado."""

    model_config = ConfigDict(frozen=True)

    game_id: str
    game_pk: Optional[int] = None
    game_date: date
    season: int
    home_team: str
    away_team: str

    # Starters
    home_starter_projected_ip: Optional[float] = None
    away_starter_projected_ip: Optional[float] = None
    home_starter_xera: Optional[float] = None
    away_starter_xera: Optional[float] = None
    home_starter_xfip: Optional[float] = None
    away_starter_xfip: Optional[float] = None
    home_starter_k_bb_pct: Optional[float] = None
    away_starter_k_bb_pct: Optional[float] = None
    home_starter_barrel_pct_allowed: Optional[float] = None
    home_starter_ip_sample: Optional[float] = None
    away_starter_ip_sample: Optional[float] = None

    # Ofensa
    home_ops: Optional[float] = None
    away_ops: Optional[float] = None
    home_ops_pa_sample: Optional[int] = None
    away_ops_pa_sample: Optional[int] = None

    # Bullpen
    home_bullpen_era: Optional[float] = None
    away_bullpen_era: Optional[float] = None
    home_bullpen_ip_last_3_days: Optional[float] = None
    away_bullpen_ip_last_3_days: Optional[float] = None
    home_closer_available: Optional[bool] = None
    away_closer_available: Optional[bool] = None

    # Lesiones
    home_key_injuries: list[str] = Field(default_factory=list)
    away_key_injuries: list[str] = Field(default_factory=list)

    # Calendario y contexto
    is_double_header: bool = False
    travel_distance: Optional[float] = None
    weather_temp_f: Optional[float] = None
    weather_wind_speed: Optional[float] = None
    park_factor: Optional[float] = None

    # Flags de calidad de dato (usados en CRI)
    starters_confirmed: bool = False
    lineups_official: bool = False
    bullpen_usage_known: bool = False
    no_last_minute_changes: bool = False

    # --- Aditivo 3.0 -> 3.1 (Seccion 3.3, ver Schema Migration Registry en
    # registries/seed.py, migracion "schema-3.0-to-3.1") ---
    # Promedios de LIGA congelados al momento del snapshot -- deliberadamente
    # parte del snapshot y no calculados en vivo dentro de un pilar: si un
    # pilar los recalculara contra la API en el momento de re-evaluar un
    # snapshot historico, un backtest futuro filtraria el promedio de liga
    # ACTUAL (de todo el resto de la temporada, incluyendo juegos posteriores
    # al que se esta recalculando) hacia atras -- exactamente la fuga de
    # informacion de punto-en-el-tiempo que el Principio 6 prohibe.
    league_avg_era: Optional[float] = None
    league_avg_ops: Optional[float] = None
    league_avg_runs_per_game: Optional[float] = None

    # --- Aditivo 3.1 -> 3.2 (Seccion 3.3, ver Schema Migration Registry en
    # registries/seed.py, migracion "schema-3.1-to-3.2") ---
    # Fielding percentage de equipo, acumulado de temporada point-in-time
    # (mismo patron que home/away_ops) -- validado via spike real contra
    # teams/{id}/stats?stats=byDateRange&group=fielding antes de
    # comprometer el esfuerzo (OAA/DRS descartados: no expuestos por
    # statsapi.mlb.com). Alimenta una senal defensiva de bajo esfuerzo en
    # el pilar team_quality, que hasta ahora solo consideraba lesiones y
    # disponibilidad de closer.
    home_fielding_pct: Optional[float] = None
    away_fielding_pct: Optional[float] = None

    # --- Aditivo 3.2 -> 3.3 (Seccion 3.3, ver Schema Migration Registry en
    # registries/seed.py, migracion "schema-3.2-to-3.3") ---
    # IP acumulada de bullpen point-in-time -- ya se calculaba internamente
    # en bullpen_era_as_of()/get_bullpen_era() (el mismo loop que suma
    # weighted_era_sum), simplemente nunca se exponia. Permite aplicar
    # shrunk_era() (la MISMA funcion de shrinkage bayesiano que ya usa
    # starter, mismo SHRINKAGE_K_IP) al ERA de bullpen -- antes de esto,
    # bullpen era el UNICO pilar de los 7 que comparaba un ERA crudo, sin
    # encoger hacia el promedio de liga por muestra chica (ver ROADMAP).
    home_bullpen_ip_sample: Optional[float] = None
    away_bullpen_ip_sample: Optional[float] = None

    schema_version: str = SCHEMA_VERSION

    # Integridad criptografica (Seccion 14.2) -- nunca provisto manualmente,
    # solo asignado por `build_game_snapshot()` abajo.
    snapshot_hash: Optional[str] = None

    def compute_hash(self) -> str:
        return hash_model_excluding(self, exclude={"snapshot_hash"})


def build_game_snapshot(**fields: object) -> GameSnapshot:
    """Unico punto de construccion valido de un `GameSnapshot` con hash.
    Calcula `snapshot_hash` sobre todos los demas campos ANTES de congelar
    el modelo -- nunca se asigna `snapshot_hash` a mano."""
    fields.pop("snapshot_hash", None)
    draft = GameSnapshot(**fields)
    return draft.model_copy(update={"snapshot_hash": draft.compute_hash()})


class PillarWeights(BaseModel):
    """Siempre normalizados a suma = 1.0 (Seccion 3.2, 6.4)."""

    starter: float
    bullpen: float
    offense: float
    team_quality: float
    context: float
    trend: float
    historical: float

    def as_dict(self) -> dict[str, float]:
        return {p: getattr(self, p) for p in SEVEN_PILLARS}

    @model_validator(mode="after")
    def _check_sum(self) -> "PillarWeights":
        total = sum(self.as_dict().values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"PillarWeights debe sumar 1.0, suma actual={total}")
        return self


class ContextSignals(BaseModel):
    """Salida del Context Detector -- Seccion 5. Solo hechos observables,
    nunca conclusiones ni pesos."""

    long_outing: bool = False
    short_outing_bullpen_game: bool = False
    bullpen_fatigue: bool = False
    key_offensive_injuries: bool = False
    double_header: bool = False
    extreme_travel: bool = False
    extreme_weather: bool = False
    small_sample_offense: bool = False
    explanations: list[str] = Field(default_factory=list)


class PillarAdvantage(BaseModel):
    """Resultado de un pilar -- Seccion 7.1."""

    pillar: str
    advantage: int = Field(ge=-2, le=2)
    explanation: str
    pillar_contract_version: str

    @model_validator(mode="after")
    def _check_discrete(self) -> "PillarAdvantage":
        if self.advantage not in (-2, -1, 0, 1, 2):
            raise ValueError("advantage debe ser un nivel discreto en {-2,-1,0,1,2}")
        return self


class WeightAuditEntry(BaseModel):
    """Seccion 6.5 -- tabla estructurada por pilar, reconstruible."""

    pillar: str
    base_weight: float
    rules_applied: list[str] = Field(default_factory=list)
    delta_total: float
    weight_before_renorm: float
    renormalization_factor: float
    final_weight: float
    human_explanation: str

    def verify(self, tolerance: float = 1e-9) -> bool:
        recomputed = self.weight_before_renorm * self.renormalization_factor
        return abs(recomputed - self.final_weight) <= tolerance


class RuleTraceEntry(BaseModel):
    """Seccion 6.6 -- entrada verificable por regla activada en una corrida."""

    rule_id: str
    trigger_signal: str
    input_data_hash: str
    supporting_experiment_id: Optional[str] = None
    scientific_justification: str
    game_id: str
    timestamp: str
    applied_to_weights: bool


class ProjectedRunsOutput(BaseModel):
    """Salida del Modulo de Carreras Proyectadas y Handicap -- Seccion 9."""

    mu_home: float
    mu_away: float
    sigma_margin: float
    prob_cover_handicap: Optional[float] = None
    projected_margin: float
    consistency_flag: Optional[Literal["aligned", "conflicting"]] = None
    variance_validated: bool = False


class FeatureContributionEntry(BaseModel):
    """Seccion 7.2."""

    pillar: str
    final_weight: float
    advantage: int
    absolute_contribution: float
    percentage_contribution: float
    dominance_warning: bool = False


class CalibrationInfo(BaseModel):
    """Seccion 8.4.1 / 11.8 -- estado real de calibracion del modelo activo.
    Mientras no exista una curva de calibracion validada con leave-one-
    season-out, `calibration_status` se mantiene en "uncalibrated" y
    `calibrated_probability` es None -- nunca se inventa un numero."""

    calibration_status: Literal["uncalibrated", "calibrated"] = "uncalibrated"
    raw_probability: Optional[float] = None
    calibrated_probability: Optional[float] = None
    calibration_bucket: Optional[str] = None
    bucket_confidence_interval: Optional[tuple[float, float]] = None


class ConfidenceGateMarketResult(BaseModel):
    """Estado del Confidence Gate para un mercado -- Seccion 10.7."""

    market_id: str
    passed: bool
    reason: str
    gate_id: Optional[str] = None
    criteria: dict[str, bool] = Field(default_factory=dict)


class RunManifest(BaseModel):
    """Seccion 14.1. Toda corrida -- en vivo o de experimento -- genera uno."""

    run_id: str
    timestamp: str
    git_commit: str
    model_version: str
    schema_version: str
    pillar_versions: dict[str, str]
    feature_registry_version: str
    rule_registry_version: str
    gate_registry_version: str
    market_registry_version: str
    input_snapshot_hash: str
    output_hash: Optional[str] = None
    config_hash: str
    random_seed: Optional[int] = None
    python_version: str
    library_versions: dict[str, str]

    # Gobernanza (Seccion 15) -- no forma parte del RunManifest "puro" del
    # spec, pero se adjunta aqui para que la corrida cargue su propio
    # veredicto de validez sin tener que recorrer el Provenance Graph.
    invalidated: bool = False
    invalidation_reasons: list[InvalidationReason] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProvenanceNode(BaseModel):
    """Seccion 14.4. Nodo de un grafo dirigido aciclico persistente,
    append-only -- nunca se poda ni se sobrescribe."""

    node_id: str
    inputs: list[str]
    outputs: list[str]
    hash: str
    timestamp: str
    version: str
    parent_nodes: list[str] = Field(default_factory=list)


class MathAuditTerm(BaseModel):
    """Un termino del desarrollo programatico de un score (Seccion 8.5)."""

    label: str
    weight: float
    value: float
    product: float


class MathAudit(BaseModel):
    formula_name: str
    terms: list[MathAuditTerm]
    total: float

    def verify(self, tolerance: float = 1e-9) -> bool:
        recomputed = sum(t.product for t in self.terms)
        return abs(recomputed - self.total) <= tolerance


class JSAReport(BaseModel):
    """JSAReport v3 -- Seccion 11, campos obligatorios de 11.8."""

    run_id: str
    game_id: str
    game_pk: Optional[int]
    game_date: date
    home_team: str
    away_team: str

    # 11.1-11.7
    pillar_advantages: list[PillarAdvantage]
    weight_audit: list[WeightAuditEntry]
    evidence_score_raw: float
    evidence_score_math_audit: MathAudit
    cri_score: float
    cri_math_audit: MathAudit
    cri_effective_base: str
    uncertainty_index: float
    uncertainty_math_audit: MathAudit
    base_weights: PillarWeights
    final_weights: PillarWeights
    rules_activated_human_readable: list[str]
    projected_runs: Optional[ProjectedRunsOutput]
    final_category: str
    confidence_gate: list[ConfidenceGateMarketResult]
    one_sentence_explanation: str

    # 11.8
    manifest_status: Literal["valid", "INVALIDATED"]
    manifest_status_reason: Optional[str] = None
    input_snapshot_hash: str
    output_hash: Optional[str] = None
    config_hash: str
    manifest: RunManifest
    rule_trace: list[RuleTraceEntry]
    feature_contribution: list[FeatureContributionEntry]
    calibration: CalibrationInfo
    monte_carlo_summary: Optional[dict] = None
    warnings: list[str] = Field(default_factory=list)
    reconstruction_token: str

    def compute_output_hash(self) -> str:
        return hash_model_excluding(self, exclude={"output_hash", "reconstruction_token", "manifest"})


# --- Entradas de Registry (Seccion 3.2 / 4.3 / 6.2 / 7.3 / 10.5bis / 3.3) ---
# Representacion Pydantic de cada fila; la persistencia append-only real
# vive en registries/db.py (tablas SQLAlchemy con el mismo shape).


class FeatureLineage(BaseModel):
    """Seccion 4.4 -- cadena de 6 eslabones, JSON estructurado."""

    source: str
    transformation: str
    shrinkage: Optional[str] = None
    feature_store_ref: str
    engine_ref: str
    report_field: str

    def is_complete(self) -> bool:
        return all([self.source, self.transformation, self.feature_store_ref, self.engine_ref, self.report_field])


class FeatureDefinition(BaseModel):
    feature_id: str
    name: str
    description: str
    source: str
    transformation: str
    real_correlation: Optional[float] = None
    model_importance: Optional[float] = None
    divergence_flag: bool = False
    shrinkage_method: Optional[str] = None
    lineage: Optional[FeatureLineage] = None
    owner: str
    date_added: str
    status: RegistryStatus = "experimental"
    version: str
    validation_experiment: Optional[str] = None
    dependencies: list[str] = Field(default_factory=list)


class RuleDefinition(BaseModel):
    rule_id: str
    trigger: str
    condition: str
    weight_adjustments: dict[str, float]
    scientific_justification: str
    version: str
    status: RegistryStatus = "experimental"
    experiments_supporting_rule: list[str] = Field(default_factory=list)
    trace_link: Optional[str] = None


class PillarRegistryEntry(BaseModel):
    pillar_id: str
    status: RegistryStatus = "experimental"
    contract_version: str
    validation_experiment: Optional[str] = None
    date_added: str


class MarketRegistryEntry(BaseModel):
    market_id: str
    description: str
    data_requirements: list[str]
    status: RegistryStatus = "experimental"
    date_added: str


class SchemaMigrationEntry(BaseModel):
    migration_id: str
    from_version: str
    to_version: str
    change_type: Literal["additive", "breaking"]
    migration_function: str
    affected_fields: list[str]
    date: str
    backward_read_compatible: bool


class GateRegistryEntry(BaseModel):
    gate_id: str
    market: str
    p_min: float
    cri_min: float
    uncertainty_max: float
    accuracy_wilson_ci: Optional[tuple[float, float]] = None
    coverage_pct: Optional[float] = None
    coverage_n: Optional[int] = None
    status: GateStatus = "under_validation"
    validation_seasons: list[int] = Field(default_factory=list)
    manifest_hash: Optional[str] = None


class ExperimentConfig(BaseModel):
    experiment_id: str
    description: str
    base_model_version: str
    feature_set: list[str]
    weights: dict[str, float]
    rules: list[str]
    date_range: tuple[str, str]
    metrics_requested: list[str]
    baseline_comparison: list[str] = Field(default_factory=list)
