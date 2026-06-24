from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
import psycopg2
import psycopg2.extras
import os
import time
import logging
import threading

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="AIOps SRE Platform API",
    description="Backend API for the AIOps demonstration platform",
    version="1.0.0"
)

# CORS - allows frontend to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics
REQUEST_COUNT = Counter(
    "api_requests_total",
    "Total API requests",
    ["method", "endpoint", "status"]
)

REQUEST_LATENCY = Histogram(
    "api_request_duration_seconds",
    "API request latency",
    ["endpoint"]
)

INCIDENT_COUNT = Counter(
    "incidents_total",
    "Total incidents created",
    ["severity"]
)

# Database connection
def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "db-service"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME", "aiops"),
        user=os.getenv("DB_USER", "aiops"),
        password=os.getenv("DB_PASSWORD", "aiops123")
    )

@app.get("/")
def root():
    return {
        "service": "AIOps SRE Platform API",
        "version": "1.0.0",
        "status": "running"
    }

@app.get("/health")
def health():
    """Health check endpoint - Kubernetes liveness probe hits this"""
    try:
        conn = get_db()
        conn.close()
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail="Database unavailable")

@app.get("/metrics")
def metrics():
    """Prometheus metrics endpoint"""
    return Response(
        generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )

@app.get("/api/incidents")
def get_incidents():
    """Get all incidents from database"""
    start_time = time.time()
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT * FROM incidents
            ORDER BY created_at DESC
            LIMIT 50
        """)
        incidents = cursor.fetchall()
        conn.close()

        REQUEST_COUNT.labels(
            method="GET",
            endpoint="/api/incidents",
            status="200"
        ).inc()

        REQUEST_LATENCY.labels(
            endpoint="/api/incidents"
        ).observe(time.time() - start_time)

        return {"incidents": [dict(i) for i in incidents]}
    except Exception as e:
        logger.error(f"Failed to get incidents: {e}")
        REQUEST_COUNT.labels(
            method="GET",
            endpoint="/api/incidents",
            status="500"
        ).inc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/incidents")
def create_incident(incident: dict):
    """Create a new incident"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO incidents
            (title, description, severity, namespace, pod_name)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (
            incident.get("title"),
            incident.get("description"),
            incident.get("severity", "medium"),
            incident.get("namespace"),
            incident.get("pod_name")
        ))
        incident_id = cursor.fetchone()[0]
        conn.commit()
        conn.close()

        INCIDENT_COUNT.labels(
            severity=incident.get("severity", "medium")
        ).inc()

        return {"id": incident_id, "status": "created"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================
# CHAOS ENDPOINTS - Used in Phase 13 for failure simulation
# ============================================================

@app.get("/chaos/crashloop")
def trigger_crashloop():
    """
    Simulates a CrashLoopBackOff by exiting the process.
    Kubernetes will restart the pod, creating a crash loop.
    """
    logger.error("CHAOS: Triggering crash loop - process will exit")
    import sys
    sys.exit(1)

@app.get("/chaos/oom")
def trigger_oom():
    """
    Simulates OOMKilled by allocating large amounts of memory.
    Kubernetes will kill the pod when it exceeds memory limits.
    """
    logger.warning("CHAOS: Triggering OOM - allocating memory")
    memory_hog = []
    while True:
        # Allocate 10MB chunks until OOM
        memory_hog.append(" " * 10 * 1024 * 1024)
        time.sleep(0.1)

@app.get("/chaos/cpu")
def trigger_cpu():
    """
    Simulates high CPU usage.
    """
    logger.warning("CHAOS: Triggering high CPU")
    def cpu_stress():
        end_time = time.time() + 60  # Run for 60 seconds
        while time.time() < end_time:
            pass  # Busy loop

    threads = []
    for _ in range(4):
        t = threading.Thread(target=cpu_stress)
        t.daemon = True
        t.start()
        threads.append(t)

    return {"status": "cpu stress started for 60 seconds"}

@app.get("/chaos/slow")
def trigger_slow():
    """
    Simulates slow response times.
    """
    logger.warning("CHAOS: Triggering slow response")
    time.sleep(30)
    return {"status": "slow response completed"}