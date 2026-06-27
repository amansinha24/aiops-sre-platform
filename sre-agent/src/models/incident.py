from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from enum import Enum

class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"

class IncidentStatus(str, Enum):
    open = "open"
    analyzing = "analyzing"
    remediating = "remediating"
    resolved = "resolved"

class K8sGPTFinding(BaseModel):
    name: str
    kind: str
    error: str
    details: str = ""
    namespace: str = ""

class RCAResult(BaseModel):
    root_cause: str
    confidence: int
    recommended_action: str
    action_type: str
    explanation: str
    safe_to_automate: bool

class Incident(BaseModel):
    id: Optional[str] = None
    title: str
    namespace: str
    resource_name: str
    resource_kind: str
    error_text: str
    severity: Severity = Severity.medium
    status: IncidentStatus = IncidentStatus.open
    rca: Optional[RCAResult] = None
    action_taken: Optional[str] = None
    created_at: datetime = datetime.now()
    resolved_at: Optional[datetime] = None
    mttr_seconds: Optional[int] = None
