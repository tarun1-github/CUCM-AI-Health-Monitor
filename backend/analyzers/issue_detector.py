"""Deterministic issue detector for CUCM health snapshots."""

from typing import List

from backend.analyzers.threshold_engine import ThresholdEngine
from backend.models.health import HealthIssue, HealthSnapshot, HealthStatus


class IssueDetector:
    """Identifies concrete, evidence-backed issues from a HealthSnapshot."""

    def __init__(self, threshold_engine: ThresholdEngine | None = None):
        self.thresholds = threshold_engine or ThresholdEngine()

    def detect(self, snapshot: HealthSnapshot) -> List[HealthIssue]:
        issues: List[HealthIssue] = []

        # Connectivity issues
        if not snapshot.connectivity.ssh_connected:
            issues.append(
                HealthIssue(
                    id="ssh_disconnected",
                    category="connectivity",
                    severity=HealthStatus.CRITICAL,
                    title="CUCM SSH CLI Connectivity Unavailable",
                    evidence=snapshot.connectivity.ssh_error or "SSH connection could not be established.",
                    impact="CLI-based system, resource, and service diagnostics could not be collected.",
                    recommended_action="Verify CUCM SSH reachability (port 22), CLI credentials, and host key configuration.",
                )
            )

        # CPU
        cpu = snapshot.system.cpu_utilization_pct
        cpu_status = self.thresholds.evaluate("cpu_utilization", cpu)
        if cpu_status in (HealthStatus.WARNING, HealthStatus.CRITICAL):
            issues.append(
                HealthIssue(
                    id="high_cpu",
                    category="system",
                    severity=cpu_status,
                    title=f"Elevated CPU Utilization ({cpu:.1f}%)",
                    evidence=f"CPU utilization measured at {cpu:.1f}%.",
                    impact="Sustained high CPU can degrade call processing and increase media/signaling latency.",
                    recommended_action="Inspect 'show process load' and 'show perf query class Process' to identify top CPU consumers.",
                )
            )

        # Memory
        mem = snapshot.system.memory_utilization_pct
        mem_status = self.thresholds.evaluate("memory_utilization", mem)
        if mem_status in (HealthStatus.WARNING, HealthStatus.CRITICAL):
            issues.append(
                HealthIssue(
                    id="high_memory",
                    category="system",
                    severity=mem_status,
                    title=f"Elevated Memory Utilization ({mem:.1f}%)",
                    evidence=f"Memory utilization measured at {mem:.1f}%.",
                    impact="High memory consumption increases swap pressure and risk of service instability.",
                    recommended_action="Review memory-intensive services and confirm swap usage via CUCM RTMT or CLI.",
                )
            )

        # Disk
        disk = snapshot.system.disk_utilization_pct
        disk_status = self.thresholds.evaluate("disk_utilization", disk)
        if disk_status in (HealthStatus.WARNING, HealthStatus.CRITICAL):
            issues.append(
                HealthIssue(
                    id="high_disk",
                    category="disk",
                    severity=disk_status,
                    title=f"Elevated Disk Utilization ({disk:.1f}%)",
                    evidence=f"Common partition disk utilization measured at {disk:.1f}%.",
                    impact="High disk usage may block trace logging, database writes, or upgrades.",
                    recommended_action="Investigate large trace/log files in activelog/common using approved Cisco cleanup procedures.",
                )
            )

        # Services
        for svc in snapshot.services:
            if svc.health in (HealthStatus.WARNING, HealthStatus.CRITICAL):
                issues.append(
                    HealthIssue(
                        id=f"svc_{svc.name.lower().replace(' ', '_')}",
                        category="services",
                        severity=svc.health,
                        title=f"Service '{svc.name}' is {svc.status}",
                        evidence=f"Service '{svc.name}' reported status [{svc.status}].",
                        impact=(
                            "Critical CUCM call-processing or platform service is unavailable."
                            if svc.is_critical
                            else "Application service is not running."
                        ),
                        recommended_action=f"Verify activation and logs for '{svc.name}' in Cisco Unified Serviceability.",
                    )
                )

        # Network diagnostics
        for diag in snapshot.network.diagnostics:
            res = diag.get("result", "").lower()
            if "fail" in res or "error" in res:
                issues.append(
                    HealthIssue(
                        id=f"diag_{diag.get('test', 'unknown').lower()}",
                        category="network",
                        severity=HealthStatus.WARNING,
                        title=f"Diagnostic Test Failed: {diag.get('test')}",
                        evidence=f"utils diagnose test '{diag.get('test')}' reported: {diag.get('result')}",
                        impact="Network, DNS, or platform validation test did not pass.",
                        recommended_action="Investigate DNS, NTP, or network reachability per Cisco CUCM diagnostic output.",
                    )
                )

        return issues
