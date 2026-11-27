"""Unified report generator facade."""

import uuid
from pathlib import Path

from backend.models.health import HealthSnapshot
from backend.models.report import ReportExportResult
from backend.reports.csv_report import write_csv_report
from backend.reports.html_report import write_html_report
from backend.reports.json_report import write_json_report
from backend.reports.pdf_report import write_pdf_report
from backend.utils.config_loader import PROJECT_ROOT


class ReportGenerator:
    """Generates HTML, PDF, CSV, and JSON reports into reports/."""

    def __init__(self, output_dir: Path | None = None):
        self.output_dir = output_dir or (PROJECT_ROOT / "reports")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, snapshot: HealthSnapshot, fmt: str) -> ReportExportResult:
        fmt_clean = fmt.lower().strip()
        ts = snapshot.collected_at.strftime("%Y%m%d_%H%M%S")
        safe_host = snapshot.device_host.replace(".", "_").replace(":", "_")
        filename = f"cucm_health_{safe_host}_{ts}_{snapshot.snapshot_id}.{fmt_clean}"
        target = self.output_dir / filename

        if fmt_clean == "html":
            write_html_report(snapshot, target)
        elif fmt_clean == "pdf":
            write_pdf_report(snapshot, target)
        elif fmt_clean == "csv":
            write_csv_report(snapshot, target)
        elif fmt_clean == "json":
            write_json_report(snapshot, target)
        else:
            raise ValueError(f"Unsupported report format: {fmt}")

        return ReportExportResult(
            report_id=uuid.uuid4().hex[:10],
            format=fmt_clean,
            file_path=str(target),
            filename=filename,
            size_bytes=target.stat().st_size,
            snapshot_id=snapshot.snapshot_id,
        )
