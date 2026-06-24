-- Create incidents table for the AIOps platform
CREATE TABLE IF NOT EXISTS incidents (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    severity VARCHAR(50) DEFAULT 'medium',
    status VARCHAR(50) DEFAULT 'open',
    namespace VARCHAR(100),
    pod_name VARCHAR(255),
    root_cause TEXT,
    ai_recommendation TEXT,
    action_taken VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);

-- Create metrics table
CREATE TABLE IF NOT EXISTS app_metrics (
    id SERIAL PRIMARY KEY,
    endpoint VARCHAR(255),
    response_time_ms INTEGER,
    status_code INTEGER,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Seed some sample data
INSERT INTO incidents (title, description, severity, status, namespace, pod_name)
VALUES
    ('Pod CrashLoopBackOff', 'API pod restarting continuously', 'high', 'open', 'application', 'api-xxx'),
    ('High Memory Usage', 'Frontend pod memory at 90%', 'medium', 'open', 'application', 'frontend-xxx'),
    ('Deployment Failed', 'Bad image tag deployed', 'critical', 'resolved', 'application', 'api-yyy');