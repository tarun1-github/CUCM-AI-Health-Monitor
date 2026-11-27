"""Health check, metric, and issue models."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class ServiceCriticality(str, Enum):
    CRITICAL = "CRITICAL"
    IMPORTANT = "IMPORTANT"
    OPTIONAL = "OPTIONAL"
    UNKNOWN = "UNKNOWN"


class SectionStatus(BaseModel):
    section: str
    status: str = "NOT COLLECTED"  # NOT COLLECTED, COLLECTED, PARTIAL, FAILED
    last_successful_at: Optional[datetime] = None
    last_attempted_at: Optional[datetime] = None
    error: Optional[str] = None
    data_freshness: str = "NOT COLLECTED"


class ConnectivityStatus(BaseModel):
    ssh_connected: bool = False
    ssh_transport: str = "none"
    ssh_prompt: Optional[str] = None
    ssh_error: Optional[str] = None
    axl_connected: bool = False
    axl_version: Optional[str] = None
    axl_error: Optional[str] = None


class CommandExecutionResult(BaseModel):
    command_id: str
    command: str
    category: str
    success: bool = False
    execution_time_ms: float = 0.0
    raw_output: str = ""
    parsed_data: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    supported: bool = True
    skipped: bool = False
    timed_out: bool = False


class SystemHealthMetrics(BaseModel):
    hostname: Optional[str] = None
    cucm_version: Optional[str] = None
    uptime: Optional[str] = None
    hardware_model: Optional[str] = None
    cpu_count: Optional[int] = None
    cpu_utilization_pct: Optional[float] = None
    cpu_idle_pct: Optional[float] = None
    load_average_1m: Optional[float] = None
    load_average_5m: Optional[float] = None
    load_average_15m: Optional[float] = None
    memory_total_mb: Optional[float] = None
    memory_used_mb: Optional[float] = None
    memory_utilization_pct: Optional[float] = None
    disk_total_gb: Optional[float] = None
    disk_used_gb: Optional[float] = None
    disk_utilization_pct: Optional[float] = None
    disk_partitions: List[Dict[str, Any]] = Field(default_factory=list)


class ServiceStatusItem(BaseModel):
    name: str
    status: str  # e.g., STARTED, STOPPED, NOT ACTIVATED
    health: HealthStatus = HealthStatus.UNKNOWN
    is_critical: bool = False
    criticality: ServiceCriticality = ServiceCriticality.UNKNOWN
    description: str = ""


class NetworkHealthMetrics(BaseModel):
    interface_name: str = "eth0"
    ip_address: Optional[str] = None
    subnet_mask: Optional[str] = None
    gateway: Optional[str] = None
    dns_primary: Optional[str] = None
    dns_secondary: Optional[str] = None
    link_status: Optional[str] = None
    mtu: Optional[int] = None
    diagnostics: List[Dict[str, str]] = Field(default_factory=list)


class ClusterNodeInfo(BaseModel):
    name: str
    host: Optional[str] = None
    role: str = "Unknown"
    status: str = "UNKNOWN"


class ClusterHealthMetrics(BaseModel):
    source: str = "none"
    publisher: Optional[str] = None
    nodes: List[ClusterNodeInfo] = Field(default_factory=list)
    replication_status: str = "UNKNOWN"


class HealthIssue(BaseModel):
    id: str
    category: str
    severity: HealthStatus
    title: str
    evidence: str
    impact: str
    recommended_action: str


class HealthScoreBreakdown(BaseModel):
    health_score: Optional[float] = None
    score_label: str = "INSUFFICIENT DATA"
    data_coverage_pct: float = 0.0
    completion_pct: float = 0.0
    reasons: List[str] = Field(default_factory=list)
    component_scores: Dict[str, Optional[float]] = Field(default_factory=dict)


class HealthClassification(BaseModel):
    overall_health: HealthStatus = HealthStatus.UNKNOWN
    cpu_health: HealthStatus = HealthStatus.UNKNOWN
    memory_health: HealthStatus = HealthStatus.UNKNOWN
    disk_health: HealthStatus = HealthStatus.UNKNOWN
    services_health: HealthStatus = HealthStatus.UNKNOWN
    network_health: HealthStatus = HealthStatus.UNKNOWN
    issues: List[HealthIssue] = Field(default_factory=list)
    score: HealthScoreBreakdown = Field(default_factory=HealthScoreBreakdown)


class AISummary(BaseModel):
    provider: str = "deterministic-fallback"
    is_ai_generated: bool = False
    overall_assessment: str = ""
    executive_summary: str = ""
    engineering_summary: str = ""
    critical_findings: List[str] = Field(default_factory=list)
    operational_impact: str = ""
    recommended_actions: List[str] = Field(default_factory=list)


class HealthSnapshot(BaseModel):
    snapshot_id: str
    device_id: str
    device_host: str
    device_role: str = "Unknown"
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_simulated: bool = False
    connectivity: ConnectivityStatus = Field(default_factory=ConnectivityStatus)
    system: SystemHealthMetrics = Field(default_factory=SystemHealthMetrics)
    services: List[ServiceStatusItem] = Field(default_factory=list)
    network: NetworkHealthMetrics = Field(default_factory=NetworkHealthMetrics)
    cluster: ClusterHealthMetrics = Field(default_factory=ClusterHealthMetrics)
    classification: HealthClassification = Field(default_factory=HealthClassification)
    ai_summary: AISummary = Field(default_factory=AISummary)
    commands: List[CommandExecutionResult] = Field(default_factory=list)
    sections: Dict[str, SectionStatus] = Field(default_factory=dict)


class TaskProgressResponse(BaseModel):
    task_id: str
    status: str = "queued"  # queued, running, completed, partial, failed, cancelled
    progress_percent: int = 0
    current_step: str = "Queued"
    completed_steps: int = 0
    total_steps: int = 0
    message: str = ""
    snapshot_id: Optional[str] = None
    error: Optional[str] = None
    cancelled: bool = False
