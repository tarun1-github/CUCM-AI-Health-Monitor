"""Interactive HTML health report generator."""

import html
from pathlib import Path
from typing import List

from backend.models.health import HealthSnapshot, HealthStatus

_STATUS_COLORS = {
    HealthStatus.HEALTHY: ("#10b981", "rgba(16, 185, 129, 0.16)"),
    HealthStatus.WARNING: ("#f59e0b", "rgba(245, 158, 11, 0.16)"),
    HealthStatus.CRITICAL: ("#ef4444", "rgba(239, 68, 68, 0.16)"),
    HealthStatus.UNKNOWN: ("#94a3b8", "rgba(148, 163, 184, 0.16)"),
}


def _badge(status: HealthStatus) -> str:
    fg, bg = _STATUS_COLORS.get(status, _STATUS_COLORS[HealthStatus.UNKNOWN])
    return f'<span class="badge" style="color:{fg};background:{bg};border:1px solid {fg};">{html.escape(status.value)}</span>'


def _fmt_pct(val) -> str:
    return f"{val:.1f}%" if val is not None else "UNKNOWN"


def render_html_report(snapshot: HealthSnapshot) -> str:
    cls = snapshot.classification
    sys = snapshot.system
    ai = snapshot.ai_summary
    net = snapshot.network

    service_rows = "\n".join(
        f"<tr><td>{html.escape(s.name)}</td><td>{html.escape(s.status)}</td>"
        f"<td>{_badge(s.health)}</td><td>{html.escape(s.description)}</td></tr>"
        for s in snapshot.services
    ) or '<tr><td colspan="4">No service data collected.</td></tr>'

    issue_rows = "\n".join(
        f"<div class='issue-card'>{_badge(i.severity)} "
        f"<strong>{html.escape(i.title)}</strong>"
        f"<p><b>Evidence:</b> {html.escape(i.evidence)}</p>"
        f"<p><b>Impact:</b> {html.escape(i.impact)}</p>"
        f"<p><b>Action:</b> {html.escape(i.recommended_action)}</p></div>"
        for i in cls.issues
    ) or "<p class='muted'>No warnings or critical findings detected.</p>"

    actions_html = "".join(f"<li>{html.escape(a)}</li>" for a in ai.recommended_actions) or "<li>None</li>"
    findings_html = "".join(f"<li>{html.escape(f)}</li>" for f in ai.critical_findings) or "<li>None</li>"

    sim_banner = (
        '<div class="sim-banner">SIMULATED / TEST FIXTURE DATA — NOT A LIVE CUCM DEVICE RESULT</div>'
        if snapshot.is_simulated
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CUCM AI Health Monitor Report — {html.escape(snapshot.device_host)}</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0a0f1e; color: #e2e8f0; margin: 0; padding: 28px; }}
  .container {{ max-width: 1120px; margin: 0 auto; }}
  .glass {{ background: rgba(20, 29, 53, 0.78); border: 1px solid rgba(99, 102, 241, 0.25); border-radius: 16px; padding: 22px; margin-bottom: 20px; }}
  .header {{ display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px; background: linear-gradient(135deg, rgba(59,130,246,0.2), rgba(139,92,246,0.2)); }}
  h1, h2, h3 {{ margin: 0 0 10px 0; color: #f8fafc; }}
  .meta-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-top: 12px; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 20px; }}
  .kpi {{ background: rgba(15, 23, 42, 0.75); border: 1px solid rgba(148, 163, 184, 0.2); border-radius: 14px; padding: 16px; }}
  .kpi-val {{ font-size: 1.7rem; font-weight: 700; margin: 8px 0; }}
  .badge {{ padding: 3px 10px; border-radius: 999px; font-size: 0.75rem; font-weight: 700; display: inline-block; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid rgba(148, 163, 184, 0.15); font-size: 0.9rem; }}
  th {{ color: #93c5fd; }}
  .issue-card {{ background: rgba(15, 23, 42, 0.7); border-left: 4px solid #6366f1; padding: 12px 16px; border-radius: 8px; margin-bottom: 10px; }}
  .sim-banner {{ background: rgba(245,158,11,0.2); border: 1px solid #f59e0b; color: #fcd34d; padding: 10px 16px; border-radius: 10px; margin-bottom: 16px; font-weight: 600; }}
  .muted {{ color: #94a3b8; }}
  pre {{ background: #050814; padding: 12px; border-radius: 8px; overflow-x: auto; font-size: 0.82rem; color: #cbd5e1; }}
</style>
</head>
<body>
<div class="container">
  {sim_banner}
  <div class="glass header">
    <div>
      <h1>CUCM AI Health Monitor — Diagnostic Report</h1>
      <div class="muted">Host: {html.escape(sys.hostname or snapshot.device_host)} ({html.escape(snapshot.device_host)}) | Role: {html.escape(snapshot.device_role)} | Version: {html.escape(sys.cucm_version or 'UNKNOWN')}</div>
      <div class="muted">Collected: {snapshot.collected_at.strftime('%Y-%m-%d %H:%M:%S UTC')} | Snapshot ID: {html.escape(snapshot.snapshot_id)}</div>
    </div>
    <div>{_badge(cls.overall_health)}</div>
  </div>

  <div class="kpi-grid">
    <div class="kpi"><div class="muted">CPU Utilization</div><div class="kpi-val">{_fmt_pct(sys.cpu_utilization_pct)}</div>{_badge(cls.cpu_health)}</div>
    <div class="kpi"><div class="muted">Memory Utilization</div><div class="kpi-val">{_fmt_pct(sys.memory_utilization_pct)}</div>{_badge(cls.memory_health)}</div>
    <div class="kpi"><div class="muted">Disk Utilization</div><div class="kpi-val">{_fmt_pct(sys.disk_utilization_pct)}</div>{_badge(cls.disk_health)}</div>
    <div class="kpi"><div class="muted">Services</div><div class="kpi-val">{sum(1 for s in snapshot.services if s.status == 'STARTED')}/{len(snapshot.services)}</div>{_badge(cls.services_health)}</div>
  </div>

  <div class="glass">
    <h2>AI Executive Summary <span class="muted" style="font-size:0.85rem;">({html.escape(ai.provider)} | AI={ai.is_ai_generated})</span></h2>
    <p><strong>Overall Assessment:</strong> {html.escape(ai.overall_assessment)}</p>
    <p>{html.escape(ai.executive_summary)}</p>
    <p><strong>Operational Impact:</strong> {html.escape(ai.operational_impact)}</p>
    <h3>Critical &amp; Key Findings</h3><ul>{findings_html}</ul>
    <h3>Recommended Actions</h3><ul>{actions_html}</ul>
  </div>

  <div class="glass">
    <h2>Detailed Technical Findings &amp; Warnings</h2>
    {issue_rows}
  </div>

  <div class="glass">
    <h2>Network &amp; Connectivity</h2>
    <div class="meta-grid">
      <div><b>Interface:</b> {html.escape(net.interface_name)}</div>
      <div><b>IP Address:</b> {html.escape(net.ip_address or 'UNKNOWN')}</div>
      <div><b>Gateway:</b> {html.escape(net.gateway or 'UNKNOWN')}</div>
      <div><b>Primary DNS:</b> {html.escape(net.dns_primary or 'UNKNOWN')}</div>
      <div><b>SSH:</b> {'Connected (' + html.escape(snapshot.connectivity.ssh_transport) + ')' if snapshot.connectivity.ssh_connected else 'Disconnected'}</div>
      <div><b>AXL:</b> {'Connected' if snapshot.connectivity.axl_connected else 'Unavailable'}</div>
    </div>
  </div>

  <div class="glass">
    <h2>CUCM Service Status</h2>
    <table>
      <thead><tr><th>Service Name</th><th>Status</th><th>Health</th><th>Description</th></tr></thead>
      <tbody>{service_rows}</tbody>
    </table>
  </div>
</div>
</body>
</html>"""


def write_html_report(snapshot: HealthSnapshot, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_html_report(snapshot), encoding="utf-8")
    return output_path
