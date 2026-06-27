from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
import logging
import time

from config import settings
from services.bedrock_service import bedrock_service
from services.k8sgpt_service import k8sgpt_service
from services.incident_service import incident_service
from models.incident import Incident, RCAResult

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="AIOps SRE Agent",
    description="Autonomous SRE Agent powered by K8sGPT + Amazon Bedrock + Claude",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics
INCIDENTS_CREATED = Counter(
    "sre_agent_incidents_total",
    "Total incidents created",
    ["severity"]
)

RCA_DURATION = Histogram(
    "sre_agent_rca_duration_seconds",
    "Time taken for RCA"
)

REMEDIATIONS_TOTAL = Counter(
    "sre_agent_remediations_total",
    "Total remediations executed",
    ["action", "status"]
)

# ============================================================
# HEALTH + METRICS
# ============================================================

@app.get("/")
def root():
    return {
        "service": "AIOps SRE Agent",
        "version": "1.0.0",
        "status": "running",
        "model": settings.bedrock_model_id
    }

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# ============================================================
# BEDROCK
# ============================================================

@app.get("/test/bedrock")
def test_bedrock():
    """Test Bedrock connectivity"""
    result = bedrock_service.test_connection()
    return result

# ============================================================
# K8sGPT FINDINGS
# ============================================================

@app.get("/findings")
def get_findings():
    """Get current K8sGPT findings"""
    findings = k8sgpt_service.get_results()
    return {"findings": findings, "count": len(findings)}

@app.get("/findings/new")
def get_new_findings():
    """Get only new K8sGPT findings"""
    findings = k8sgpt_service.get_new_results()
    return {"findings": findings, "count": len(findings)}

# ============================================================
# INCIDENTS
# ============================================================

@app.get("/incidents")
def get_incidents():
    """Get all incidents"""
    incidents = incident_service.get_all_incidents()
    return {
        "incidents": [i.dict() for i in incidents],
        "count": len(incidents)
    }

@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str):
    """Get specific incident"""
    incident = incident_service.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident.dict()

# ============================================================
# CORE AIOPS WORKFLOW
# ============================================================

@app.post("/analyze")
def analyze_findings(background_tasks: BackgroundTasks):
    """
    Main AIOps workflow:
    1. Fetch K8sGPT findings
    2. Send to Claude for RCA
    3. Create incidents
    4. Auto-remediate if safe
    """
    findings = k8sgpt_service.get_new_results()
    
    if not findings:
        return {"message": "No new findings to analyze", "incidents": []}

    created_incidents = []

    for finding in findings:
        try:
            logger.info(f"Analyzing finding: {finding.get('name')}")
            
            # Step 1: Create incident
            incident = incident_service.create_incident(finding)
            INCIDENTS_CREATED.labels(severity=incident.severity.value).inc()

            # Step 2: Get RCA from Claude
            start_time = time.time()
            rca = bedrock_service.analyze_finding(finding)
            RCA_DURATION.observe(time.time() - start_time)

            # Step 3: Update incident with RCA
            incident_service.update_incident_rca(incident.id, rca)

            # Step 4: Auto-remediate if safe
            if (rca.safe_to_automate and 
                rca.confidence >= settings.confidence_threshold and
                rca.action_type in settings.safe_actions and
                settings.auto_remediation_enabled):

                logger.info(f"Auto-remediating: {rca.action_type} for {finding.get('name')}")
                
                # Extract deployment name from finding
                name_parts = finding.get("name", "/").split("/")
                namespace = name_parts[0] if len(name_parts) > 0 else "application"
                resource = name_parts[1] if len(name_parts) > 1 else ""

                if rca.action_type == "restart_deployment":
                    result = k8sgpt_service.restart_deployment(namespace, resource)
                elif rca.action_type == "rollback_deployment":
                    result = k8sgpt_service.rollback_deployment(namespace, resource)
                else:
                    result = {"status": "skipped"}

                action_status = result.get("status", "unknown")
                REMEDIATIONS_TOTAL.labels(
                    action=rca.action_type,
                    status=action_status
                ).inc()

                if action_status == "success":
                    incident_service.resolve_incident(incident.id, rca.action_type)

            created_incidents.append({
                "incident_id": incident.id,
                "title": incident.title,
                "severity": incident.severity.value,
                "rca": rca.dict(),
                "auto_remediated": rca.safe_to_automate
            })

        except Exception as e:
            logger.error(f"Failed to analyze finding {finding.get('name')}: {e}")
            continue

    return {
        "analyzed": len(created_incidents),
        "incidents": created_incidents
    }

@app.post("/remediate/{incident_id}")
def manual_remediate(incident_id: str, action: str):
    """
    Manually trigger remediation for an incident.
    Used when auto-remediation is disabled or confidence is low.
    """
    incident = incident_service.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    if action not in settings.safe_actions:
        raise HTTPException(
            status_code=400,
            detail=f"Action {action} not in safe actions list: {settings.safe_actions}"
        )

    if action == "restart_deployment":
        result = k8sgpt_service.restart_deployment(
            incident.namespace,
            incident.resource_name
        )
    elif action == "rollback_deployment":
        result = k8sgpt_service.rollback_deployment(
            incident.namespace,
            incident.resource_name
        )

    if result.get("status") == "success":
        incident_service.resolve_incident(incident_id, action)
        REMEDIATIONS_TOTAL.labels(action=action, status="success").inc()

    return {
        "incident_id": incident_id,
        "action": action,
        "result": result
    }
