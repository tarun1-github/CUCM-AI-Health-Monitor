"""Tests for AI LLM integration and deterministic fallback summarizer."""

import json
from unittest.mock import MagicMock

from backend.ai.report_summarizer import ReportSummarizer
from backend.analyzers.health_analyzer import HealthAnalyzer
from backend.models.health import (
    ConnectivityStatus,
    HealthSnapshot,
    HealthStatus,
    ServiceStatusItem,
    SystemHealthMetrics,
)


def _sample_snapshot() -> HealthSnapshot:
    snap = HealthSnapshot(
        snapshot_id="ai01",
        device_id="cucm-primary",
        device_host="10.197.206.141",
        is_simulated=True,
        connectivity=ConnectivityStatus(ssh_connected=True, ssh_transport="netmiko"),
        system=SystemHealthMetrics(
            cucm_version="15.0.1.10000-14",
            cpu_utilization_pct=78.0,
            memory_utilization_pct=82.0,
            disk_utilization_pct=91.0,
        ),
        services=[
            ServiceStatusItem(name="Cisco CallManager", status="STARTED", health=HealthStatus.HEALTHY, is_critical=True),
        ],
    )
    return HealthAnalyzer().analyze(snap, persist=False)


def test_deterministic_fallback_summary():
    snap = _sample_snapshot()
    summarizer = ReportSummarizer(llm_client=None)
    res = summarizer.summarize(snap, provider="none")
    assert res.ai_summary.is_ai_generated is False
    assert res.ai_summary.provider == "deterministic-fallback"
    assert "91.0%" in res.ai_summary.executive_summary
    assert len(res.ai_summary.recommended_actions) >= 1


def test_llm_client_json_response_parsed():
    snap = _sample_snapshot()
    mock_llm = MagicMock()
    mock_llm.provider_name = "mock-llm"
    mock_llm.generate.return_value = json.dumps({
        "overall_assessment": "System requires attention due to elevated disk usage.",
        "executive_summary": "Disk utilization is 91%.",
        "engineering_summary": "CPU 78%, Mem 82%, Disk 91%.",
        "critical_findings": ["Disk utilization at 91%"],
        "operational_impact": "Log truncation risk.",
        "recommended_actions": ["Review common partition logs."],
    })
    res = ReportSummarizer(llm_client=mock_llm).summarize(snap)
    assert res.ai_summary.is_ai_generated is True
    assert res.ai_summary.provider == "mock-llm"
    assert "91%" in res.ai_summary.executive_summary
