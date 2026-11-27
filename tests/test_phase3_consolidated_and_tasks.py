"""Tests for Phase 3 consolidated dashboard state, background tasks, session recovery, and scoring."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from backend.analyzers.health_analyzer import HealthAnalyzer
from backend.api.dependencies import SnapshotStore
from backend.collectors.cucm_health import CUCMHealthCollector
from backend.collectors.service_health import parse_service_list
from backend.main import app
from backend.models.health import (
    ConnectivityStatus,
    HealthSnapshot,
    HealthStatus,
    NetworkHealthMetrics,
    ServiceCriticality,
    ServiceStatusItem,
    SystemHealthMetrics,
)


def test_consolidated_store_preserves_untouched_sections():
    store = SnapshotStore(max_items=10)
    store._snapshots.clear()

    sys_snap = HealthSnapshot(
        snapshot_id="s1",
        device_id="cucm-primary",
        device_host="10.197.206.141",
        connectivity=ConnectivityStatus(ssh_connected=True, ssh_transport="netmiko"),
        system=SystemHealthMetrics(hostname="UCM15-HQ-PUB", cpu_utilization_pct=25.0, memory_utilization_pct=70.0, disk_utilization_pct=33.0),
        network=NetworkHealthMetrics(ip_address="10.197.206.141", gateway="10.197.206.1"),
    )
    store.add(sys_snap)

    # Service-only partial update must preserve system and network
    svc_partial = HealthSnapshot(
        snapshot_id="s2",
        device_id="cucm-primary",
        device_host="10.197.206.141",
        connectivity=ConnectivityStatus(ssh_connected=True, ssh_transport="netmiko"),
        services=[ServiceStatusItem(name="Cisco CallManager", status="STARTED", health=HealthStatus.HEALTHY, is_critical=True)],
    )
    merged = store.merge_partial(svc_partial, updated_sections={"connectivity", "services"})
    store.add(merged)

    assert merged.system.hostname == "UCM15-HQ-PUB"
    assert merged.system.cpu_utilization_pct == 25.0
    assert merged.network.ip_address == "10.197.206.141"
    assert len(merged.services) == 1

    # System-only partial update must preserve services and network
    sys_partial2 = HealthSnapshot(
        snapshot_id="s3",
        device_id="cucm-primary",
        device_host="10.197.206.141",
        connectivity=ConnectivityStatus(ssh_connected=True, ssh_transport="netmiko"),
        system=SystemHealthMetrics(hostname="UCM15-HQ-PUB", cpu_utilization_pct=30.0),
    )
    merged2 = store.merge_partial(sys_partial2, updated_sections={"connectivity", "system", "cpu"})
    assert merged2.system.cpu_utilization_pct == 30.0
    assert len(merged2.services) == 1
    assert merged2.network.ip_address == "10.197.206.141"


def test_scoring_coverage_unknown_and_service_criticality():
    # Insufficient data case
    empty_snap = HealthSnapshot(snapshot_id="e1", device_id="c1", device_host="10.197.206.141")
    analyzed_empty = HealthAnalyzer().analyze(empty_snap, persist=False)
    assert analyzed_empty.classification.score.health_score is None
    assert analyzed_empty.classification.score.score_label == "INSUFFICIENT DATA"
    assert analyzed_empty.classification.score.data_coverage_pct == 0.0

    # Partial coverage with warning service
    svcs = parse_service_list(
        "Cisco CallManager[STARTED]\nCisco Certificate Enrollment Service[STOPPED]  Component is not running\n",
        critical_services={"Cisco CallManager"},
        optional_services={"Cisco Certificate Enrollment Service"},
    )
    assert svcs[0].criticality == ServiceCriticality.CRITICAL
    assert svcs[1].criticality == ServiceCriticality.OPTIONAL
    assert svcs[1].health == HealthStatus.WARNING

    snap = HealthSnapshot(
        snapshot_id="s4",
        device_id="c1",
        device_host="10.197.206.141",
        connectivity=ConnectivityStatus(ssh_connected=True, ssh_transport="netmiko"),
        system=SystemHealthMetrics(cpu_utilization_pct=20.0, memory_utilization_pct=70.0),
        services=svcs,
    )
    analyzed = HealthAnalyzer().analyze(snap, persist=False)
    assert analyzed.classification.score.health_score is not None
    assert 0 < analyzed.classification.score.data_coverage_pct < 100.0


def test_collector_timeout_marks_timed_out_and_continues():
    mock_ssh = MagicMock()
    mock_ssh.is_connected.return_value = True
    mock_ssh.get_prompt.return_value = "admin:"

    def fake_send(cmd, timeout=45):
        if "status" in cmd:
            raise TimeoutError("Pattern not detected: 'admin:' in output")
        return "Active Master Version: 15.0.1.12900-234"

    mock_ssh.send_command.side_effect = fake_send
    mock_axl = MagicMock()
    mock_axl.get_cucm_version.return_value = "15.0.1.12900-234"
    mock_axl.get_cluster_nodes.return_value = [{"name": "UCM15-HQ-PUB", "node_role": "Publisher"}]

    collector = CUCMHealthCollector(ssh_client=mock_ssh, axl_client=mock_axl)
    snap = collector.collect(command_ids=["show_status", "show_version_active"])
    assert len(snap.commands) == 2
    status_cmd = next(c for c in snap.commands if c.command_id == "show_status")
    ver_cmd = next(c for c in snap.commands if c.command_id == "show_version_active")
    assert status_cmd.timed_out is True
    assert status_cmd.success is False
    assert ver_cmd.success is True


def test_full_health_task_endpoints():
    client = TestClient(app)
    r = client.get("/api/health/tasks/nonexistent")
    assert r.status_code == 404
    assert client.get("/api/health/system").status_code == 200
    assert client.get("/api/health/services").status_code == 200
    assert client.get("/api/health/network").status_code == 200
    assert client.get("/api/health/cluster").status_code == 200
