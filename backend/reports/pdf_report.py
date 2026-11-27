"""Professional PDF health report generator using ReportLab."""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from backend.models.health import HealthSnapshot, HealthStatus

_RL_COLORS = {
    HealthStatus.HEALTHY: colors.HexColor("#10b981"),
    HealthStatus.WARNING: colors.HexColor("#f59e0b"),
    HealthStatus.CRITICAL: colors.HexColor("#ef4444"),
    HealthStatus.UNKNOWN: colors.HexColor("#64748b"),
}


def write_pdf_report(snapshot: HealthSnapshot, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(output_path), pagesize=letter, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleCustom", parent=styles["Heading1"], textColor=colors.HexColor("#1e293b"), fontSize=18)
    h2_style = ParagraphStyle("H2Custom", parent=styles["Heading2"], textColor=colors.HexColor("#1e3a8a"), fontSize=13, spaceBefore=12)
    body = styles["BodyText"]

    cls = snapshot.classification
    sys = snapshot.system
    ai = snapshot.ai_summary
    elements = []

    elements.append(Paragraph("CUCM AI Health Monitor — Diagnostic Report", title_style))
    if snapshot.is_simulated:
        elements.append(Paragraph("<b>[SIMULATED / TEST FIXTURE DATA]</b>", body))
    elements.append(Spacer(1, 8))

    meta_data = [
        ["Device Host", snapshot.device_host, "Hostname", sys.hostname or "UNKNOWN"],
        ["CUCM Version", sys.cucm_version or "UNKNOWN", "Role", snapshot.device_role],
        ["Collected At", snapshot.collected_at.strftime("%Y-%m-%d %H:%M:%S UTC"), "Overall Health", cls.overall_health.value],
        ["CPU Utilization", f"{sys.cpu_utilization_pct:.1f}%" if sys.cpu_utilization_pct is not None else "UNKNOWN",
         "Memory Utilization", f"{sys.memory_utilization_pct:.1f}%" if sys.memory_utilization_pct is not None else "UNKNOWN"],
        ["Disk Utilization", f"{sys.disk_utilization_pct:.1f}%" if sys.disk_utilization_pct is not None else "UNKNOWN",
         "Uptime", sys.uptime or "UNKNOWN"],
    ]
    meta_table = Table(meta_data, colWidths=[110, 155, 110, 155])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#e2e8f0")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#e2e8f0")),
        ("TEXTCOLOR", (3, 2), (3, 2), _RL_COLORS.get(cls.overall_health, colors.black)),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (3, 2), (3, 2), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(meta_table)

    elements.append(Paragraph(f"AI Executive Summary ({ai.provider})", h2_style))
    elements.append(Paragraph(ai.overall_assessment or "N/A", body))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(ai.executive_summary or "N/A", body))

    if ai.recommended_actions:
        elements.append(Paragraph("Recommended Actions", h2_style))
        for action in ai.recommended_actions:
            elements.append(Paragraph(f"• {action}", body))

    if cls.issues:
        elements.append(Paragraph("Detected Issues & Warnings", h2_style))
        for issue in cls.issues:
            elements.append(Paragraph(f"<b>[{issue.severity.value}] {issue.title}</b>: {issue.evidence}", body))

    if snapshot.services:
        elements.append(Paragraph("CUCM Services", h2_style))
        svc_rows = [["Service Name", "Status", "Health"]]
        for svc in snapshot.services[:40]:
            svc_rows.append([svc.name, svc.status, svc.health.value])
        svc_table = Table(svc_rows, colWidths=[260, 140, 130])
        svc_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("PADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(svc_table)

    doc.build(elements)
    return output_path
