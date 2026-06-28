from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from fastapi.responses import HTMLResponse
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

@app.get("/ui", response_class=HTMLResponse)
def incident_dashboard():
    """
    Standalone AIOps Incident Dashboard UI
    Shows all incidents with Claude RCA details
    """
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AIOps SRE Platform - Incident Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: 'Segoe UI', Arial, sans-serif;
            background: #0a0e1a;
            color: #e0e0e0;
            min-height: 100vh;
        }

        .header {
            background: linear-gradient(135deg, #1a1f35, #0d1b2a);
            border-bottom: 2px solid #e94560;
            padding: 20px 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .header h1 {
            color: #e94560;
            font-size: 1.8em;
            font-weight: 700;
        }

        .header p {
            color: #888;
            font-size: 0.9em;
            margin-top: 4px;
        }

        .live-badge {
            background: #e94560;
            color: white;
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 0.8em;
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        .container {
            max-width: 1400px;
            margin: 30px auto;
            padding: 0 20px;
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 15px;
            margin-bottom: 30px;
        }

        .stat-card {
            background: #1a1f35;
            border: 1px solid #2a2f4a;
            border-radius: 10px;
            padding: 20px;
            text-align: center;
            transition: transform 0.2s;
        }

        .stat-card:hover { transform: translateY(-3px); }

        .stat-card .number {
            font-size: 2.5em;
            font-weight: bold;
            color: #e94560;
            margin-bottom: 5px;
        }

        .stat-card .number.green { color: #00c853; }
        .stat-card .number.blue { color: #2979ff; }
        .stat-card .number.orange { color: #ff6d00; }
        .stat-card .number.purple { color: #aa00ff; }

        .stat-card .label {
            color: #888;
            font-size: 0.85em;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .section-title {
            font-size: 1.2em;
            color: #e94560;
            margin-bottom: 15px;
            padding-bottom: 8px;
            border-bottom: 1px solid #2a2f4a;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .refresh-btn {
            background: #e94560;
            color: white;
            border: none;
            padding: 6px 15px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 0.8em;
        }

        .refresh-btn:hover { background: #c73652; }

        .incidents-list {
            display: flex;
            flex-direction: column;
            gap: 15px;
        }

        .incident-card {
            background: #1a1f35;
            border: 1px solid #2a2f4a;
            border-radius: 10px;
            padding: 20px;
            border-left: 4px solid #e94560;
            transition: transform 0.2s;
        }

        .incident-card:hover { transform: translateX(3px); }
        .incident-card.resolved { border-left-color: #00c853; opacity: 0.85; }
        .incident-card.analyzing { border-left-color: #ff6d00; }
        .incident-card.remediating { border-left-color: #2979ff; }

        .incident-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 12px;
        }

        .incident-title {
            font-size: 1.1em;
            font-weight: 600;
            color: #fff;
        }

        .badges {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }

        .badge {
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 0.75em;
            font-weight: 600;
            text-transform: uppercase;
        }

        .badge.critical { background: #b71c1c; color: white; }
        .badge.high { background: #e65100; color: white; }
        .badge.medium { background: #f57f17; color: white; }
        .badge.low { background: #1b5e20; color: white; }
        .badge.open { background: #b71c1c; color: white; }
        .badge.analyzing { background: #e65100; color: white; }
        .badge.resolved { background: #1b5e20; color: white; }
        .badge.remediating { background: #0d47a1; color: white; }

        .incident-body {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
            margin-top: 12px;
        }

        .rca-section {
            background: #0d1b2a;
            border-radius: 8px;
            padding: 15px;
        }

        .rca-section h4 {
            color: #2979ff;
            font-size: 0.85em;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 8px;
        }

        .rca-section p {
            color: #ccc;
            font-size: 0.9em;
            line-height: 1.5;
        }

        .action-section {
            background: #0d1b2a;
            border-radius: 8px;
            padding: 15px;
        }

        .action-section h4 {
            color: #aa00ff;
            font-size: 0.85em;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 8px;
        }

        .confidence-bar {
            background: #2a2f4a;
            border-radius: 10px;
            height: 8px;
            margin: 8px 0;
            overflow: hidden;
        }

        .confidence-fill {
            height: 100%;
            border-radius: 10px;
            background: linear-gradient(90deg, #e94560, #ff6d00, #00c853);
            transition: width 1s ease;
        }

        .confidence-text {
            font-size: 0.85em;
            color: #888;
        }

        .action-badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 5px;
            font-size: 0.8em;
            font-weight: 600;
            margin-top: 5px;
        }

        .action-restart { background: #0d47a1; color: white; }
        .action-rollback { background: #1b5e20; color: white; }
        .action-manual { background: #37474f; color: white; }

        .incident-footer {
            margin-top: 12px;
            padding-top: 12px;
            border-top: 1px solid #2a2f4a;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.8em;
            color: #666;
        }

        .mttr-badge {
            background: #00c853;
            color: white;
            padding: 3px 10px;
            border-radius: 12px;
            font-weight: 600;
        }

        .no-incidents {
            text-align: center;
            padding: 60px;
            color: #444;
        }

        .no-incidents .icon { font-size: 3em; margin-bottom: 15px; }

        .auto-refresh {
            color: #444;
            font-size: 0.8em;
            text-align: center;
            margin-top: 20px;
            padding: 10px;
        }

        .explanation-box {
            background: #0a0e1a;
            border: 1px solid #2a2f4a;
            border-radius: 6px;
            padding: 10px;
            margin-top: 8px;
            font-size: 0.85em;
            color: #aaa;
            line-height: 1.6;
            max-height: 100px;
            overflow-y: auto;
        }

        .loading {
            text-align: center;
            padding: 40px;
            color: #444;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        .spinner {
            display: inline-block;
            width: 30px;
            height: 30px;
            border: 3px solid #2a2f4a;
            border-top-color: #e94560;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-bottom: 10px;
        }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>🤖 AIOps SRE Platform</h1>
            <p>Autonomous Incident Detection • AI-Powered RCA • Safe Auto-Remediation</p>
        </div>
        <div>
            <span class="live-badge">● LIVE</span>
        </div>
    </div>

    <div class="container">
        <!-- Stats Section -->
        <div class="stats-grid" id="stats">
            <div class="stat-card">
                <div class="number" id="total-incidents">-</div>
                <div class="label">Total Incidents</div>
            </div>
            <div class="stat-card">
                <div class="number" id="active-incidents">-</div>
                <div class="label">Active</div>
            </div>
            <div class="stat-card">
                <div class="number green" id="resolved-incidents">-</div>
                <div class="label">Resolved</div>
            </div>
            <div class="stat-card">
                <div class="number blue" id="avg-confidence">-</div>
                <div class="label">Avg AI Confidence</div>
            </div>
            <div class="stat-card">
                <div class="number orange" id="avg-mttr">-</div>
                <div class="label">Avg MTTR</div>
            </div>
        </div>

        <!-- Incidents Section -->
        <div class="section-title">
            📋 Incident Timeline
            <button class="refresh-btn" onclick="loadData()">🔄 Refresh</button>
        </div>

        <div class="incidents-list" id="incidents-list">
            <div class="loading">
                <div class="spinner"></div>
                <div>Loading incidents...</div>
            </div>
        </div>

        <div class="auto-refresh" id="refresh-timer">
            Auto-refreshing every 30 seconds
        </div>
    </div>

    <script>
        function getActionClass(actionType) {
            if (!actionType) return 'action-manual';
            if (actionType.includes('restart')) return 'action-restart';
            if (actionType.includes('rollback')) return 'action-rollback';
            return 'action-manual';
        }

        function getActionLabel(actionType) {
            const labels = {
                'restart_deployment': '🔄 Restart Deployment',
                'rollback_deployment': '⏪ Rollback Deployment',
                'manual_intervention': '👤 Manual Intervention',
                'scale_deployment': '📈 Scale Deployment'
            };
            return labels[actionType] || actionType || 'Unknown';
        }

        function getSeverityEmoji(severity) {
            const emojis = {
                'critical': '🔴',
                'high': '🟠',
                'medium': '🟡',
                'low': '🟢'
            };
            return emojis[severity] || '⚪';
        }

        function formatDate(dateStr) {
            if (!dateStr) return 'Unknown';
            const date = new Date(dateStr);
            return date.toLocaleString();
        }

        function formatMTTR(seconds) {
            if (!seconds) return null;
            if (seconds < 60) return seconds + 's';
            if (seconds < 3600) return Math.round(seconds/60) + 'm';
            return Math.round(seconds/3600) + 'h';
        }

        function renderIncident(incident) {
            const rca = incident.rca || {};
            const confidence = rca.confidence || 0;
            const statusClass = incident.status || 'open';
            const mttr = formatMTTR(incident.mttr_seconds);

            return `
                <div class="incident-card ${statusClass}">
                    <div class="incident-header">
                        <div class="incident-title">
                            ${getSeverityEmoji(incident.severity)} ${incident.title || 'Unknown Incident'}
                        </div>
                        <div class="badges">
                            <span class="badge ${incident.severity}">${(incident.severity || 'unknown').toUpperCase()}</span>
                            <span class="badge ${incident.status}">${(incident.status || 'unknown').toUpperCase()}</span>
                            ${incident.action_taken ? '<span class="badge resolved">✅ AUTO-REMEDIATED</span>' : ''}
                        </div>
                    </div>

                    <div style="font-size:0.85em; color:#666; margin-bottom:10px;">
                        📦 ${incident.resource_kind || 'Unknown'} / ${incident.resource_name || 'Unknown'} 
                        in <strong style="color:#aaa">${incident.namespace || 'Unknown'}</strong>
                        &nbsp;•&nbsp; 🕐 ${formatDate(incident.created_at)}
                    </div>

                    <div style="background:#0d1b2a; border-radius:6px; padding:10px; margin-bottom:12px; font-size:0.85em; color:#ff6d00;">
                        ⚠️ <strong>Error:</strong> ${incident.error_text || 'No error details'}
                    </div>

                    <div class="incident-body">
                        <div class="rca-section">
                            <h4>🧠 Claude's Root Cause Analysis</h4>
                            <p>${rca.root_cause || 'Analysis pending...'}</p>
                            ${rca.explanation ? `
                                <div class="explanation-box">
                                    💡 ${rca.explanation}
                                </div>
                            ` : ''}
                        </div>

                        <div class="action-section">
                            <h4>⚡ AI Recommendation</h4>
                            <p style="color:#ccc; font-size:0.9em;">${rca.recommended_action || 'Awaiting analysis...'}</p>
                            
                            <div class="confidence-bar">
                                <div class="confidence-fill" style="width:${confidence}%"></div>
                            </div>
                            <div class="confidence-text">
                                AI Confidence: <strong style="color:#fff">${confidence}%</strong>
                            </div>

                            <div>
                                <span class="action-badge ${getActionClass(rca.action_type)}">
                                    ${getActionLabel(rca.action_type)}
                                </span>
                            </div>

                            <div style="margin-top:8px; font-size:0.8em; color:#666;">
                                Safe to automate: 
                                <strong style="color:${rca.safe_to_automate ? '#00c853' : '#e94560'}">
                                    ${rca.safe_to_automate ? '✅ Yes' : '❌ No'}
                                </strong>
                            </div>
                        </div>
                    </div>

                    <div class="incident-footer">
                        <div>
                            🆔 ${incident.id ? incident.id.substring(0,8) + '...' : 'N/A'}
                            &nbsp;•&nbsp;
                            Action taken: <strong style="color:#aaa">${incident.action_taken || 'None'}</strong>
                        </div>
                        <div>
                            ${incident.resolved_at ? `
                                ✅ Resolved: ${formatDate(incident.resolved_at)}
                                ${mttr ? `<span class="mttr-badge">MTTR: ${mttr}</span>` : ''}
                            ` : ''}
                        </div>
                    </div>
                </div>
            `;
        }

        async function loadData() {
            try {
                const response = await fetch('/dashboard/summary');
                const data = await response.json();

                // Update stats
                document.getElementById('total-incidents').textContent = data.total_incidents || 0;
                document.getElementById('active-incidents').textContent = data.active_incidents || 0;
                document.getElementById('resolved-incidents').textContent = data.resolved_incidents || 0;
                document.getElementById('avg-confidence').textContent = 
                    (data.avg_ai_confidence || 0).toFixed(0) + '%';
                document.getElementById('avg-mttr').textContent = 
                    data.avg_mttr_seconds > 0 ? 
                    formatMTTR(data.avg_mttr_seconds) : '—';

                // Update incidents
                const incidentsList = document.getElementById('incidents-list');
                
                if (!data.incidents || data.incidents.length === 0) {
                    incidentsList.innerHTML = `
                        <div class="no-incidents">
                            <div class="icon">✅</div>
                            <div>No incidents found. System is healthy!</div>
                            <div style="margin-top:10px; color:#333; font-size:0.85em;">
                                Deploy a broken pod to simulate a failure and trigger AI analysis
                            </div>
                        </div>
                    `;
                } else {
                    // Sort by created_at descending (newest first)
                    const sorted = data.incidents.sort((a, b) => 
                        new Date(b.created_at) - new Date(a.created_at)
                    );
                    incidentsList.innerHTML = sorted.map(renderIncident).join('');
                }

                // Update refresh time
                document.getElementById('refresh-timer').textContent = 
                    `Last updated: ${new Date().toLocaleTimeString()} • Auto-refreshing every 30s`;

            } catch (error) {
                document.getElementById('incidents-list').innerHTML = `
                    <div class="no-incidents">
                        <div class="icon">❌</div>
                        <div>Failed to load data: ${error.message}</div>
                    </div>
                `;
            }
        }

        // Load on page load
        loadData();

        // Auto-refresh every 30 seconds
        setInterval(loadData, 30000);
    </script>
</body>
</html>
"""
