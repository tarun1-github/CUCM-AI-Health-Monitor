"""Regression tests for Overall Health Score, Data Coverage, Check Completion, and SnapshotStore backfill."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.analyzers.health_analyzer import HealthAnalyzer
from backend.api.dependencies import ALL_SECTIONS, SnapshotStore, default_sections
from backend.main import app
from backend.models.health import (
    ConnectivityStatus,
    HealthSnapshot,
    HealthStatus,
    NetworkHealthMetrics,
    SectionStatus,
    ServiceCriticality,
    ServiceStatusItem,
    SystemHealthMetrics,
)


def _make_full_snapshot(include_ai: bool = True) -> HealthSnapshot:
    secs = default_sections()
    for k in ALL_SECTIONS:
        if k == "ai_summary" and not include_ai:
            secs[k] = SectionStatus(section=k, status="NOT COLLECTED")
        else:
            secs[k] = SectionStatus(section=k, status="COLLECTED")
    return HealthSnapshot(
        snapshot_id="full01",
        device_id="cucm-primary",
        device_host="10.197.206.141",
        connectivity=ConnectivityStatus(ssh_connected=True, ssh_transport="netmiko", axl_connected=True),
        system=SystemHealthMetrics(
            hostname="UCM15-HQ-PUB",
            cpu_utilization_pct=16.9,
            memory_utilization_pct=75.2,
            disk_utilization_pct=33.0,
        ),
        services=[
            ServiceStatusItem(name="Cisco CallManager", status="STARTED", health=HealthStatus.HEALTHY, is_critical=True, criticality=ServiceCriticality.CRITICAL),
            ServiceStatusItem(name="Cisco Certificate Enrollment Service", status="STOPPED", health=HealthStatus.WARNING, criticality=ServiceCriticality.OPTIONAL),
        ],
        network=NetworkHealthMetrics(ip_address="10.197.206.141", diagnostics=[{"test": "tomcat", "result": "Passed"}]),
        sections=secs,
    )


def test_all_nine_sections_completed_and_weighted_score():
    snap = _make_full_snapshot(include_ai=True)
    analyzed = HealthAnalyzer().analyze(snap, persist=False)
    sc = analyzed.classification.score
    assert sc.completion_pct == 100.0
    assert sc.completed_sections == 9
    assert sc.total_sections == 9
    assert sc.completion_status == "COMPLETED"
    assert sc.data_coverage_pct == 100.0
    # CPU=100*0.2 + Mem=100*0.2 + Disk=100*0.2 + Svc=50*0.25 + Net=100*0.15 = 87.5
    assert sc.health_score == 87.5
    assert sc.score_label == "87.5/100"
    assert sc.missing_sections == []


def test_missing_ai_summary_does_not_reduce_technical_coverage():
    snap = _make_full_snapshot(include_ai=False)
    analyzed = HealthAnalyzer().analyze(snap, persist=False)
    sc = analyzed.classification.score
    assert sc.data_coverage_pct == 100.0
    assert sc.completed_sections == 8
    assert sc.total_sections == 9
    assert sc.completion_status == "PARTIAL"
    assert "ai_summary" in sc.missing_sections


def test_partial_unknown_metrics_and_insufficient_data():
    empty = HealthSnapshot(snapshot_id="empty", device_id="c1", device_host="10.197.206.141", sections=default_sections())
    res_empty = HealthAnalyzer().analyze(empty, persist=False).classification.score
    assert res_empty.health_score is None
    assert res_empty.score_label == "INSUFFICIENT DATA"
    assert res_empty.data_coverage_pct == 0.0
    assert res_empty.completion_pct == 0.0

    partial = HealthSnapshot(
        snapshot_id="part",
        device_id="c1",
        device_host="10.197.206.141",
        system=SystemHealthMetrics(cpu_utilization_pct=20.0),
    )
    res_part = HealthAnalyzer().analyze(partial, persist=False).classification.score
    assert res_part.health_score == 100.0
    assert res_part.data_coverage_pct == 20.0
    assert "memory" in res_part.invalid_metrics


def test_legacy_snapshot_json_without_score_is_reanalyzed_on_load(tmp_path, monkeypatch):
    proc_dir = tmp_path / "data" / "processed"
    proc_dir.mkdir(parents=True)
    snap = _make_full_snapshot(include_ai=True)
    raw_dict = json.loads(snap.model_dump_json())
    raw_dict["classification"].pop("score", None)
    (proc_dir / "legacy.json").write_text(json.dumps(raw_dict), encoding="utf-8")

    monkeypatch.setattr("backend.api.dependencies.PROJECT_ROOT", tmp_path)
    store = SnapshotStore()
    loaded = store.latest()
    assert loaded is not None
    assert loaded.classification.score.health_score == 87.5
    assert loaded.classification.score.data_coverage_pct == 100.0
    assert loaded.classification.score.completion_pct == 100.0


def test_api_latest_and_frontend_kpi_binding():
    client = TestClient(app)
    r = client.get("/api/health/latest")
    assert r.status_code == 200
    js = Path("frontend/assets/js/dashboard.js").read_text(encoding="utf-8")
    assert "score.score_label" in js
    assert "score.data_coverage_pct" in js
    assert "score.completion_pct" in js
    assert "score.completed_sections" in js
