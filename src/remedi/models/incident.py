from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field


class IncidentSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentType(str, Enum):
    FRESHNESS = "freshness"
    DATA_QUALITY = "data_quality"
    SCHEMA_DRIFT = "schema_drift"
    LINEAGE_BREAK = "lineage_break"


class SchemaField(BaseModel):
    name: str
    type: str
    description: str | None = None
    nullable: bool = True


class EntityRef(BaseModel):
    urn: str
    name: str
    type: str
    platform: str | None = None
    owners: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    description: str | None = None
    schema_fields: list[SchemaField] = Field(default_factory=list)


class Incident(BaseModel):
    id: str
    title: str
    type: IncidentType
    severity: IncidentSeverity
    entity: EntityRef
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    details: dict[str, Any] = Field(default_factory=dict)
    sample_queries: list[str] = Field(default_factory=list)
    status: Literal["open", "resolved"] = "open"
    detection_source: str = "incident"  # incident | assertion | search


class DownstreamImpact(BaseModel):
    entity: EntityRef
    hop: int
    impact_reason: str


class BlastRadius(BaseModel):
    source_urn: str
    downstream: list[DownstreamImpact] = Field(default_factory=list)
    upstream: list[DownstreamImpact] = Field(default_factory=list)
    owner_notify: list[str] = Field(default_factory=list)
    dashboard_count: int = 0
    dataset_count: int = 0
    ml_model_count: int = 0
    ml_feature_count: int = 0
    edges: list[dict[str, Any]] = Field(default_factory=list)  # for graph UI
    column_impacts: list[dict[str, Any]] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_impacted(self) -> int:
        return len(self.downstream)

    @property
    def ml_models(self) -> list[DownstreamImpact]:
        return [d for d in self.downstream if d.entity.type.lower() == "mlmodel"]


class GeneratedArtifact(BaseModel):
    path: str
    kind: str
    description: str
    content: str
    grounded_in: list[str] = Field(default_factory=list)
    grounded: bool | None = None


class WriteBackAction(BaseModel):
    action: str
    status: Literal["ok", "dry_run", "fallback", "error"] = "ok"
    detail: str = ""


class CatalogWriteBack(BaseModel):
    urn: str
    actions: list[str] = Field(default_factory=list)
    results: list[WriteBackAction] = Field(default_factory=list)
    document_title: str | None = None
    dry_run: bool = False
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)


class RemediationPlan(BaseModel):
    summary: str
    root_cause: str
    steps: list[str]
    artifacts: list[GeneratedArtifact]
    write_back: CatalogWriteBack
    codegen_mode: Literal["template", "llm"] = "template"


class RunStep(BaseModel):
    name: str
    status: Literal["pending", "running", "done", "error"] = "done"
    detail: str = ""
    duration_ms: int = 0


class ToolCall(BaseModel):
    """Judge-visible DataHub / MCP-shaped tool audit entry."""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: Literal["ok", "fallback", "error", "simulated"] = "ok"
    detail: str = ""
    duration_ms: int = 0


class TriageItem(BaseModel):
    incident: Incident
    blast_radius: BlastRadius
    risk_score: int = Field(ge=0, le=100)
    priority: Literal["P0", "P1", "P2", "P3"]
    reasons: list[str] = Field(default_factory=list)
    recommended_action: str


class TriageReport(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    items: list[TriageItem] = Field(default_factory=list)
    top_incident_id: str | None = None
    tools_used: list[ToolCall] = Field(default_factory=list)


class OperationalAction(BaseModel):
    kind: str
    status: Literal["planned", "recorded", "sent", "error"]
    destination: str
    summary: str
    detail: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class RemediationResult(BaseModel):
    run_id: str
    incident: Incident
    blast_radius: BlastRadius
    plan: RemediationPlan
    mode: str
    success: bool = True
    message: str = ""
    timeline: list[RunStep] = Field(default_factory=list)
    tools_used: list[ToolCall] = Field(default_factory=list)
    pending_write_back: bool = False
    groundedness: dict[str, Any] = Field(default_factory=dict)
    proposal_id: str | None = None
    proposal_digest: str | None = None
    proposal_integrity: Literal["legacy", "sealed"] = "legacy"
    actions_taken: list[OperationalAction] = Field(default_factory=list)
