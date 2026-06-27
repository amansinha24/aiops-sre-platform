import logging
import uuid
from datetime import datetime
from typing import List, Optional
from models.incident import Incident, IncidentStatus, Severity, RCAResult

logger = logging.getLogger(__name__)

class IncidentService:
    def __init__(self):
        # In-memory store for incidents
        # In production this would be a database
        self.incidents: dict = {}

    def create_incident(self, finding: dict, rca: Optional[RCAResult] = None) -> Incident:
        """Create a new incident from a K8sGPT finding"""
        
        # Extract namespace and resource name from finding
        name_parts = finding.get("name", "/").split("/")
        namespace = name_parts[0] if len(name_parts) > 0 else "unknown"
        resource_name = name_parts[1] if len(name_parts) > 1 else "unknown"

        # Determine severity from error text
        error_text = finding.get("error", "").lower()
        if "crashloop" in error_text or "oomkill" in error_text:
            severity = Severity.critical
        elif "error" in error_text or "failed" in error_text:
            severity = Severity.high
        else:
            severity = Severity.medium

        incident = Incident(
            id=str(uuid.uuid4()),
            title=f"{finding.get('kind', 'Unknown')} issue: {resource_name}",
            namespace=namespace,
            resource_name=resource_name,
            resource_kind=finding.get("kind", "Unknown"),
            error_text=finding.get("error", ""),
            severity=severity,
            status=IncidentStatus.open,
            rca=rca
        )

        self.incidents[incident.id] = incident
        logger.info(f"Created incident {incident.id}: {incident.title}")
        return incident

    def update_incident_rca(self, incident_id: str, rca: RCAResult) -> Optional[Incident]:
        """Update incident with RCA results"""
        incident = self.incidents.get(incident_id)
        if incident:
            incident.rca = rca
            incident.status = IncidentStatus.analyzing
            logger.info(f"Updated incident {incident_id} with RCA")
        return incident

    def resolve_incident(self, incident_id: str, action_taken: str) -> Optional[Incident]:
        """Mark incident as resolved"""
        incident = self.incidents.get(incident_id)
        if incident:
            incident.status = IncidentStatus.resolved
            incident.action_taken = action_taken
            incident.resolved_at = datetime.now()
            if incident.created_at:
                incident.mttr_seconds = int(
                    (incident.resolved_at - incident.created_at).total_seconds()
                )
            logger.info(f"Resolved incident {incident_id} - MTTR: {incident.mttr_seconds}s")
        return incident

    def get_all_incidents(self) -> List[Incident]:
        """Get all incidents"""
        return list(self.incidents.values())

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        """Get specific incident"""
        return self.incidents.get(incident_id)

# Singleton instance
incident_service = IncidentService()
