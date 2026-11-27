"""Tests for HTML, PDF, CSV, and JSON report generation."""

from backend.ai.report_summarizer import ReportSummarizer
from backend.analyzers.health_analyzer import HealthAnalyzer
from backend.models.health import ConnectivityStatus, HealthSnapshot, SystemHealthMetrics
from backend.reports.report_generator import ReportGenerator


def test_generate_all_report_formats(tmp_path):
    snap = HealthSnapshot(
        snapshot_id="rep01",
        device_id="cucm-primary",
        device_host="10.197.206.141",
        is_simulated=True,
        connectivity=ConnectivityStatus(ssh_connected=True, ssh_transport="netmiko"),
        system=SystemHealthMetrics(
            hostname="cucm-pub-01",
            cucm_version="15.0.1.10000-14",
            cpu_utilization_pct=35.0,
            memory_utilization_pct=55.0,
            disk_utilization_pct=60.0,
        ),
    )
    snap = HealthAnalyzer().analyze(snap, persist=False)
    snap = ReportSummarizer(llm_client=None).summarize(snap, provider="none")

    gen = ReportGenerator(output_dir=tmp_path)
    for fmt in ("html", "pdf", "csv", "json"):
        res = gen.generate(snap, fmt)
        assert res.size_bytes > 0
        assert (tmp_path / res.filename).exists()

    html_content = (tmp_path / gen.generate(snap, "html").filename).read_text(encoding="utf-8")
    assert "CUCM AI Health Monitor" in html_content
    assert "10.197.206.141" in html_content
