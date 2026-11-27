"""Deterministic health analyzer combining thresholds, services, and network state."""

from typing import Dict, List, Optional

from backend.analyzers.issue_detector import IssueDetector
from backend.analyzers.threshold_engine import ThresholdEngine
from backend.models.health import (
    HealthClassification,
    HealthScoreBreakdown,
    HealthSnapshot,
    HealthStatus,
)
from backend.utils.config_loader import PROJECT_ROOT, load_settings_yaml

_SEVERITY_RANK = {
    HealthStatus.UNKNOWN: 0,
    HealthStatus.HEALTHY: 1,
    HealthStatus.WARNING: 2,
    HealthStatus.CRITICAL: 3,
}

_STATUS_POINTS = {
    HealthStatus.HEALTHY: 100.0,
    HealthStatus.WARNING: 50.0,
    HealthStatus.CRITICAL: 0.0,
}

_DEFAULT_WEIGHTS: Dict[str, float] = {
    "cpu": 0.20,
    "memory": 0.20,
    "disk": 0.20,
    "services": 0.25,
    "network": 0.15,
}


class HealthAnalyzer:
    """Computes deterministic HealthClassification and HealthScoreBreakdown for a HealthSnapshot."""

    def __init__(self, threshold_engine: ThresholdEngine | None = None):
        self.thresholds = threshold_engine or ThresholdEngine()
        self.detector = IssueDetector(self.thresholds)
        yaml_cfg = load_settings_yaml()
        self.weights: Dict[str, float] = yaml_cfg.get("scoring_weights", _DEFAULT_WEIGHTS)

    def analyze(self, snapshot: HealthSnapshot, persist: bool = True) -> HealthSnapshot:
        cpu_health = self.thresholds.evaluate("cpu_utilization", snapshot.system.cpu_utilization_pct)
        mem_health = self.thresholds.evaluate("memory_utilization", snapshot.system.memory_utilization_pct)
        disk_health = self.thresholds.evaluate("disk_utilization", snapshot.system.disk_utilization_pct)

        services_health = self._classify_services(snapshot)
        network_health = self._classify_network(snapshot)
        issues = self.detector.detect(snapshot)

        component_states = [cpu_health, mem_health, disk_health, services_health, network_health]
        known_states = [s for s in component_states if s != HealthStatus.UNKNOWN]

        if not snapshot.connectivity.ssh_connected and not known_states:
            overall = HealthStatus.CRITICAL if snapshot.connectivity.ssh_error else HealthStatus.UNKNOWN
        elif not known_states:
            overall = HealthStatus.UNKNOWN
        else:
            overall = max(known_states, key=lambda s: _SEVERITY_RANK[s])

        # Escalate if any critical issue exists
        if any(i.severity == HealthStatus.CRITICAL for i in issues):
            overall = HealthStatus.CRITICAL

        score = self._compute_score(
            snapshot=snapshot,
            components={
                "cpu": cpu_health,
                "memory": mem_health,
                "disk": disk_health,
                "services": services_health,
                "network": network_health,
            },
            issues=issues,
        )

        snapshot.classification = HealthClassification(
            overall_health=overall,
            cpu_health=cpu_health,
            memory_health=mem_health,
            disk_health=disk_health,
            services_health=services_health,
            network_health=network_health,
            issues=issues,
            score=score,
        )

        if persist:
            self._save_processed(snapshot)
        return snapshot

    def _compute_score(
        self,
        snapshot: HealthSnapshot,
        components: Dict[str, HealthStatus],
        issues: list,
    ) -> HealthScoreBreakdown:
        total_weight = sum(self.weights.get(k, 0.2) for k in components)
        known_weight = 0.0
        weighted_sum = 0.0
        comp_scores: Dict[str, Optional[float]] = {}

        for name, status in components.items():
            pts = _STATUS_POINTS.get(status)
            comp_scores[name] = pts
            if pts is not None:
                w = float(self.weights.get(name, 0.2))
                known_weight += w
                weighted_sum += pts * w

        data_coverage_pct = round((known_weight / total_weight) * 100.0, 1) if total_weight > 0 else 0.0

        # Completion percentage across the 9 dashboard sections
        if snapshot.sections:
            done_sections = sum(
                1 for sec in snapshot.sections.values() if sec.status in ("COLLECTED", "PARTIAL")
            )
            completion_pct = round((done_sections / max(len(snapshot.sections), 1)) * 100.0, 1)
        else:
            completion_pct = data_coverage_pct

        reasons: List[str] = []
        if known_weight == 0.0:
            reasons.append("Insufficient telemetry collected to calculate health score.")
            return HealthScoreBreakdown(
                health_score=None,
                score_label="INSUFFICIENT DATA",
                data_coverage_pct=0.0,
                completion_pct=completion_pct,
                reasons=reasons,
                component_scores=comp_scores,
            )

        health_score = round(weighted_sum / known_weight, 1)
        for name, status in components.items():
            if status == HealthStatus.UNKNOWN:
                reasons.append(f"{name.title()} metric not collected (excluded from score).")
            elif status == HealthStatus.WARNING:
                reasons.append(f"{name.title()} is in WARNING state (50/100).")
            elif status == HealthStatus.CRITICAL:
                reasons.append(f"{name.title()} is in CRITICAL state (0/100).")

        for issue in issues[:3]:
            if issue.title not in reasons:
                reasons.append(issue.title)

        if not reasons:
            reasons.append("All evaluated components are healthy.")

        return HealthScoreBreakdown(
            health_score=health_score,
            score_label=f"{health_score:.0f}/100",
            data_coverage_pct=data_coverage_pct,
            completion_pct=completion_pct,
            reasons=reasons,
            component_scores=comp_scores,
        )

    @staticmethod
    def _classify_services(snapshot: HealthSnapshot) -> HealthStatus:
        if not snapshot.services:
            return HealthStatus.UNKNOWN
        if any(s.health == HealthStatus.CRITICAL for s in snapshot.services):
            return HealthStatus.CRITICAL
        if any(s.health == HealthStatus.WARNING for s in snapshot.services):
            return HealthStatus.WARNING
        return HealthStatus.HEALTHY

    @staticmethod
    def _classify_network(snapshot: HealthSnapshot) -> HealthStatus:
        diags = snapshot.network.diagnostics
        has_ip = bool(snapshot.network.ip_address)
        if not diags and not has_ip:
            return HealthStatus.UNKNOWN
        if any("fail" in d.get("result", "").lower() or "error" in d.get("result", "").lower() for d in diags):
            return HealthStatus.WARNING
        return HealthStatus.HEALTHY

    @staticmethod
    def _save_processed(snapshot: HealthSnapshot) -> None:
        proc_dir = PROJECT_ROOT / "data" / "processed"
        proc_dir.mkdir(parents=True, exist_ok=True)
        ts = snapshot.collected_at.strftime("%Y%m%d_%H%M%S")
        safe_host = snapshot.device_host.replace(".", "_").replace(":", "_")
        path = proc_dir / f"{safe_host}_{ts}_{snapshot.snapshot_id}.json"
        path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
