from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
import logging
import time

from config import settings
from services.bedrock_service import bedrock_service
from services.k8sgpt_service import k8sgpt_service
from services.incident_service import incident_service
from models.incident import Incident, RCAResult

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

# ============================================================
# PROMETHEUS METRICS
# ============================================================

INCIDENTS_CREATED = Counter(
    "sre_agent_incidents_total",
    "Total incidents created",
    ["severity", "namespace"]
)

RCA_DURATION = Histogram(
    "sre_agent_rca_duration_seconds",
    "Time taken for RCA",
    buckets=[1, 2, 5, 10, 30, 60]
)

REMEDIATIONS_TOTAL = Counter(
    "sre_agent_remediations_total",
    "Total remediations executed",
    ["action", "status", "namespace"]
)

AI_CONFIDENCE = Gauge(
    "sre_agent_ai_confidence_score",
    "AI confidence score for last RCA",
    ["namespace", "resource"]
)

ACTIVE_INCIDENTS = Gauge(
    "sre_agent_active_incidents",
    "Number of active incidents"
)

MTTR_SECONDS = Gauge(
    "sre_agent_mttr_seconds",
    "Mean time to resolution in seconds",
    ["namespace"]
)

FINDINGS_TOTAL = Gauge(
    "sre_agent_k8sgpt_findings_total",
    "Total K8sGPT findings currently active"
)

BEDROCK_CALLS = Counter(
    "sre_agent_bedrock_calls_total",
    "Total Bedrock API calls",
    ["status"]
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
    # Update active incidents gauge
    all_incidents = incident_service.get_all_incidents()
    active = [i for i in all_incidents if i.status.value != "resolved"]
    ACTIVE_INCIDENTS.set(len(active))

    # Update findings gauge
    findings = k8sgpt_service.get_results()
    FINDINGS_TOTAL.set(len(findings))

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# ============================================================
# BEDROCK
# ============================================================

@app.get("/test/bedrock")
def test_bedrock():
    result = bedrock_service.test_connection()
    return result

# ============================================================
# K8sGPT FINDINGS
# ============================================================

@app.get("/findings")
def get_findings():
    findings = k8sgpt_service.get_results()
    return {"findings": findings, "count": len(findings)}

@app.get("/findings/new")
def get_new_findings():
    findings = k8sgpt_service.get_new_results()
    return {"findings": findings, "count": len(findings)}

# ============================================================
# INCIDENTS
# ============================================================

@app.get("/incidents")
def get_incidents():
    incidents = incident_service.get_all_incidents()
    return {
        "incidents": [i.dict() for i in incidents],
        "count": len(incidents)
    }

@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str):
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

            # Extract namespace
            name_parts = finding.get("name", "/").split("/")
            namespace = name_parts[0] if len(name_parts) > 0 else "application"
            resource = name_parts[1] if len(name_parts) > 1 else "unknown"

            # Step 1: Create incident
            incident = incident_service.create_incident(finding)
            INCIDENTS_CREATED.labels(
                severity=incident.severity.value,
                namespace=namespace
            ).inc()

            # Step 2: Get RCA from Claude
            start_time = time.time()
            try:
                rca = bedrock_service.analyze_finding(finding)
                duration = time.time() - start_time
                RCA_DURATION.observe(duration)
                BEDROCK_CALLS.labels(status="success").inc()
                logger.info(f"RCA completed in {duration:.2f}s confidence={rca.confidence}")
            except Exception as e:
                BEDROCK_CALLS.labels(status="failed").inc()
                raise e

            # Step 3: Update incident with RCA
            incident_service.update_incident_rca(incident.id, rca)

            # Update confidence gauge
            AI_CONFIDENCE.labels(
                namespace=namespace,
                resource=resource
            ).set(rca.confidence)

            # Step 4: Auto-remediate if safe
            if (rca.safe_to_automate and
                rca.confidence >= settings.confidence_threshold and
                rca.action_type in settings.safe_actions and
                settings.auto_remediation_enabled):

                logger.info(f"Auto-remediating: {rca.action_type}")

                if rca.action_type == "restart_deployment":
                    result = k8sgpt_service.restart_deployment(namespace, resource)
                elif rca.action_type == "rollback_deployment":
                    result = k8sgpt_service.rollback_deployment(namespace, resource)
                else:
                    result = {"status": "skipped"}

                action_status = result.get("status", "unknown")
                REMEDIATIONS_TOTAL.labels(
                    action=rca.action_type,
                    status=action_status,
                    namespace=namespace
                ).inc()

                if action_status == "success":
                    resolved = incident_service.resolve_incident(
                        incident.id,
                        rca.action_type
                    )
                    if resolved and resolved.mttr_seconds:
                        MTTR_SECONDS.labels(namespace=namespace).set(
                            resolved.mttr_seconds
                        )

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
    incident = incident_service.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    if action not in settings.safe_actions:
        raise HTTPException(
            status_code=400,
            detail=f"Action {action} not in safe actions: {settings.safe_actions}"
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
        resolved = incident_service.resolve_incident(incident_id, action)
        if resolved and resolved.mttr_seconds:
            MTTR_SECONDS.labels(namespace=incident.namespace).set(
                resolved.mttr_seconds
            )
        REMEDIATIONS_TOTAL.labels(
            action=action,
            status="success",
            namespace=incident.namespace
        ).inc()

    return {
        "incident_id": incident_id,
        "action": action,
        "result": result
    }

# ============================================================
# DASHBOARD DATA API
# ============================================================

@app.get("/dashboard/summary")
def dashboard_summary():
    """Summary data for the AIOps dashboard"""
    all_incidents = incident_service.get_all_incidents()
    findings = k8sgpt_service.get_results()

    total = len(all_incidents)
    active = len([i for i in all_incidents if i.status.value != "resolved"])
    resolved = len([i for i in all_incidents if i.status.value == "resolved"])
    auto_remediated = len([i for i in all_incidents if i.action_taken is not None])

    mttr_list = [i.mttr_seconds for i in all_incidents if i.mttr_seconds]
    avg_mttr = sum(mttr_list) / len(mttr_list) if mttr_list else 0

    avg_confidence = 0
    if all_incidents:
        confidences = [i.rca.confidence for i in all_incidents if i.rca]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0

    return {
        "total_incidents": total,
        "active_incidents": active,
        "resolved_incidents": resolved,
        "auto_remediated": auto_remediated,
        "avg_mttr_seconds": round(avg_mttr, 2),
        "avg_ai_confidence": round(avg_confidence, 2),
        "active_findings": len(findings),
        "incidents": [i.dict() for i in all_incidents]
    }
