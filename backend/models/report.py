"""Report export metadata models."""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


class ReportExportResult(BaseModel):
    report_id: str
    format: str  # html, pdf, csv, json
    file_path: str
    filename: str
    size_bytes: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    snapshot_id: Optional[str] = None
