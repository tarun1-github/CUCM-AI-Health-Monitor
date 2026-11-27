"""Report generation and download routes."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.api.dependencies import get_snapshot_store
from backend.models.report import ReportExportResult
from backend.reports.report_generator import ReportGenerator
from backend.utils.config_loader import PROJECT_ROOT

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.post("/export/{fmt}", response_model=ReportExportResult)
def export_report(fmt: str) -> ReportExportResult:
    snapshot = get_snapshot_store().latest()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Run a health check before exporting a report.")
    if fmt.lower() not in ("html", "pdf", "csv", "json"):
        raise HTTPException(status_code=400, detail=f"Unsupported format: {fmt}")
    generator = ReportGenerator()
    return generator.generate(snapshot, fmt)


@router.get("/download/{filename}")
def download_report(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    target = PROJECT_ROOT / "reports" / safe_name
    if not target.exists():
        raise HTTPException(status_code=404, detail="Report file not found.")
    return FileResponse(path=str(target), filename=safe_name)
