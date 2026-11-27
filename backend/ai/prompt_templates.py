"""Prompt templates for grounded CUCM health interpretation."""

import json
from backend.models.health import HealthSnapshot


SYSTEM_PROMPT = """You are a Senior Cisco Unified Communications (CUCM) Engineer.
Your task is to interpret structured CUCM health-check JSON and explain it clearly in natural language.

STRICT RULES:
1. NEVER fabricate metrics, percentages, service names, or errors not present in the input JSON.
2. NEVER claim an issue exists without direct evidence in the input JSON.
3. NEVER treat null/UNKNOWN/missing metrics as HEALTHY. Explicitly state when data is unavailable.
4. Do NOT recommend running configuration-changing commands without noting Cisco-approved procedures.
5. Return ONLY valid JSON with these exact keys:
   - "overall_assessment": string (1-2 sentences)
   - "executive_summary": string (concise non-technical summary for leadership)
   - "engineering_summary": string (detailed technical interpretation)
   - "critical_findings": list of strings
   - "operational_impact": string
   - "recommended_actions": list of strings
"""


def build_health_prompt(snapshot: HealthSnapshot) -> str:
    """Serialize key health metrics (excluding raw noisy dumps) for LLM interpretation."""
    compact = {
        "device_host": snapshot.device_host,
        "device_role": snapshot.device_role,
        "collected_at": snapshot.collected_at.isoformat(),
        "is_simulated": snapshot.is_simulated,
        "connectivity": snapshot.connectivity.model_dump(),
        "system": snapshot.system.model_dump(exclude={"disk_partitions"}),
        "disk_partitions": snapshot.system.disk_partitions,
        "services_summary": {
            "total": len(snapshot.services),
            "started": sum(1 for s in snapshot.services if s.status == "STARTED"),
            "non_started": [
                {"name": s.name, "status": s.status, "health": s.health.value, "critical": s.is_critical}
                for s in snapshot.services
                if s.status != "STARTED"
            ],
        },
        "network": snapshot.network.model_dump(),
        "cluster": snapshot.cluster.model_dump(),
        "classification": snapshot.classification.model_dump(),
    }
    return (
        "Analyze the following Cisco CUCM health-check JSON and respond with the required JSON structure:\n\n"
        + json.dumps(compact, indent=2, default=str)
    )
