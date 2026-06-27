def build_rca_prompt(finding: dict) -> str:
    """
    Build a structured SRE prompt for Claude.
    The quality of this prompt directly determines RCA quality.
    """
    return f"""You are a Senior Site Reliability Engineer with 10 years of Kubernetes experience.

Analyze this Kubernetes issue and provide a structured root cause analysis.

ISSUE DETAILS:
- Resource Type: {finding.get("kind", "Unknown")}
- Resource Name: {finding.get("name", "Unknown")}
- Namespace: {finding.get("namespace", "Unknown")}
- Error: {finding.get("error", "Unknown")}
- Additional Details: {finding.get("details", "None")}

Respond in this EXACT JSON format (no other text):
{{
    "root_cause": "Clear explanation of why this is happening",
    "confidence": <integer 0-100>,
    "recommended_action": "Specific action to take",
    "action_type": "<one of: restart_deployment, rollback_deployment, scale_deployment, manual_intervention>",
    "explanation": "Step by step explanation of the issue",
    "safe_to_automate": <true or false>
}}

RULES:
- confidence above 80 means you are very sure
- safe_to_automate is true ONLY for restart_deployment and rollback_deployment
- If unsure, set action_type to manual_intervention
- Be specific and actionable
"""

def build_remediation_prompt(incident: dict, action: str) -> str:
    """Build prompt for remediation validation"""
    return f"""You are a Senior SRE reviewing a remediation action.

INCIDENT: {incident.get("title")}
PROPOSED ACTION: {action}
RESOURCE: {incident.get("resource_name")} in {incident.get("namespace")}

Is this remediation action safe to execute? 
Respond in JSON:
{{
    "approved": <true or false>,
    "reason": "explanation",
    "risks": "potential risks of this action"
}}
"""
