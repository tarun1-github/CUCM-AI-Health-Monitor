"""Generates AI-powered or deterministic fallback health summaries."""

import json
from typing import Optional

from backend.ai.llm_factory import BaseLLMClient, create_llm_client
from backend.ai.prompt_templates import SYSTEM_PROMPT, build_health_prompt
from backend.models.health import AISummary, HealthSnapshot, HealthStatus
from backend.utils.logger import get_logger

logger = get_logger("summarizer", category="ai")


class ReportSummarizer:
    """Translates structured HealthSnapshot into natural-language assessment."""

    def __init__(self, llm_client: Optional[BaseLLMClient] = None):
        self._llm_client = llm_client

    def summarize(self, snapshot: HealthSnapshot, provider: Optional[str] = None) -> HealthSnapshot:
        client = self._llm_client or create_llm_client(provider=provider)
        if client is not None:
            try:
                user_prompt = build_health_prompt(snapshot)
                raw = client.generate(SYSTEM_PROMPT, user_prompt)
                parsed = self._parse_llm_json(raw)
                snapshot.ai_summary = AISummary(
                    provider=client.provider_name,
                    is_ai_generated=True,
                    overall_assessment=parsed.get("overall_assessment", ""),
                    executive_summary=parsed.get("executive_summary", ""),
                    engineering_summary=parsed.get("engineering_summary", ""),
                    critical_findings=parsed.get("critical_findings", []),
                    operational_impact=parsed.get("operational_impact", ""),
                    recommended_actions=parsed.get("recommended_actions", []),
                )
                logger.info("Generated AI summary using provider=%s", client.provider_name)
                return snapshot
            except Exception as exc:
                logger.warning("LLM summarization failed (%s); using deterministic fallback.", exc)

        snapshot.ai_summary = self._deterministic_fallback(snapshot)
        return snapshot

    @staticmethod
    def _parse_llm_json(raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(line for line in lines if not line.strip().startswith("```"))
        return json.loads(text)

    @staticmethod
    def _deterministic_fallback(snapshot: HealthSnapshot) -> AISummary:
        cls = snapshot.classification
        sys = snapshot.system
        issues = cls.issues

        if cls.overall_health == HealthStatus.HEALTHY:
            overall = f"CUCM node {snapshot.device_host} is operating normally with all measured indicators within healthy thresholds."
        elif cls.overall_health == HealthStatus.WARNING:
            overall = f"CUCM node {snapshot.device_host} requires attention due to {len(issues)} warning condition(s)."
        elif cls.overall_health == HealthStatus.CRITICAL:
            overall = f"CUCM node {snapshot.device_host} is in a CRITICAL state requiring immediate engineering review."
        else:
            overall = f"Health status for CUCM node {snapshot.device_host} is UNKNOWN because required telemetry could not be collected."

        metric_parts = []
        metric_parts.append(
            f"CPU utilization is {sys.cpu_utilization_pct:.1f}%"
            if sys.cpu_utilization_pct is not None
            else "CPU utilization is UNKNOWN"
        )
        metric_parts.append(
            f"Memory utilization is {sys.memory_utilization_pct:.1f}%"
            if sys.memory_utilization_pct is not None
            else "Memory utilization is UNKNOWN"
        )
        metric_parts.append(
            f"Disk utilization is {sys.disk_utilization_pct:.1f}%"
            if sys.disk_utilization_pct is not None
            else "Disk utilization is UNKNOWN"
        )

        started = sum(1 for s in snapshot.services if s.status == "STARTED")
        total_svc = len(snapshot.services)
        svc_sentence = (
            f"{started} of {total_svc} discovered services are Started."
            if total_svc > 0
            else "Service status data is NOT AVAILABLE."
        )

        exec_summary = f"{overall} " + "; ".join(metric_parts) + f". {svc_sentence}"

        eng_lines = [
            f"Target: {snapshot.device_host} ({snapshot.device_role}) | Version: {sys.cucm_version or 'UNKNOWN'} | Uptime: {sys.uptime or 'UNKNOWN'}.",
            "Resource Metrics: " + ", ".join(metric_parts) + ".",
            f"Services: {svc_sentence}",
            f"SSH: {'Connected (' + snapshot.connectivity.ssh_transport + ')' if snapshot.connectivity.ssh_connected else 'Disconnected'} | AXL: {'Connected' if snapshot.connectivity.axl_connected else 'Unavailable'}.",
        ]

        findings = [f"[{i.severity.value}] {i.title} — {i.evidence}" for i in issues]
        if not findings and cls.overall_health == HealthStatus.UNKNOWN:
            findings.append("[UNKNOWN] Telemetry could not be collected from the target device.")

        impact = (
            " ".join(dict.fromkeys(i.impact for i in issues))
            if issues
            else (
                "No active service or resource degradation detected in collected telemetry."
                if cls.overall_health == HealthStatus.HEALTHY
                else "Operational impact cannot be determined without live telemetry."
            )
        )

        actions = list(dict.fromkeys(i.recommended_action for i in issues))
        if not actions:
            actions = (
                ["Continue routine monitoring and schedule periodic health checks."]
                if cls.overall_health == HealthStatus.HEALTHY
                else ["Verify SSH/AXL connectivity and credentials, then re-run the health check."]
            )

        return AISummary(
            provider="deterministic-fallback",
            is_ai_generated=False,
            overall_assessment=overall,
            executive_summary=exec_summary,
            engineering_summary="\n".join(eng_lines),
            critical_findings=findings,
            operational_impact=impact,
            recommended_actions=actions,
        )
