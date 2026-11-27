"""Tests for deterministic health classification and issue detection."""

from backend.analyzers.health_analyzer import HealthAnalyzer
from backend.models.health import (
    ConnectivityStatus,
    HealthSnapshot,
    HealthStatus,
    ServiceStatusItem,
    SystemHealthMetrics,
)


def test_analyzer_classifies_warning_and_critical():
    snap = HealthSnapshot(
        snapshot_id="test01",
        device_id="cucm-primary",
        device_host="10.197.206.141",
        is_simulated=True,
        connectivity=ConnectivityStatus(ssh_connected=True, ssh_transport="netmiko"),
        system=SystemHealthMetrics(
            cpu_utilization_pct=82.0,
            memory_utilization_pct=86.0,
            disk_utilization_pct=91.0,
        ),
        services=[
            ServiceStatusItem(name="Cisco CallManager", status="STARTED", health=HealthStatus.HEALTHY, is_critical=True),
        ],
    )
    analyzed = HealthAnalyzer().analyze(snap, persist=False)
    assert analyzed.classification.cpu_health == HealthStatus.WARNING
    assert analyzed.classification.memory_health == HealthStatus.WARNING
    assert analyzed.classification.disk_health == HealthStatus.CRITICAL
    assert analyzed.classification.overall_health == HealthStatus.CRITICAL
    assert len(analyzed.classification.issues) == 3


def test_analyzer_marks_missing_metrics_as_unknown():
    snap = HealthSnapshot(
        snapshot_id="test02",
        device_id="cucm-primary",
        device_host="10.197.206.141",
        is_simulated=True,
        connectivity=ConnectivityStatus(ssh_connected=True, ssh_transport="paramiko"),
        system=SystemHealthMetrics(),
    )
    analyzed = HealthAnalyzer().analyze(snap, persist=False)
    assert analyzed.classification.cpu_health == HealthStatus.UNKNOWN
    assert analyzed.classification.overall_health == HealthStatus.UNKNOWN
