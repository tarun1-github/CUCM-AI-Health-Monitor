"""Parsers for CUCM system, version, status, and hardware CLI outputs."""

import re
from typing import Any, Dict


def parse_show_version_active(raw: str) -> Dict[str, Any]:
    """Parse 'show version active' output."""
    result: Dict[str, Any] = {}
    match = re.search(r"Active\s+Master\s+Version:\s*(.+)", raw, re.IGNORECASE)
    if match:
        result["cucm_version"] = match.group(1).strip()
    else:
        ver = re.search(r"(\d+\.\d+\.\d+\.\d+(?:-\d+)?)", raw)
        if ver:
            result["cucm_version"] = ver.group(1)
    return result


def parse_show_status(raw: str) -> Dict[str, Any]:
    """Parse 'show status' output for hostname, uptime, CPU, memory, and disk."""
    data: Dict[str, Any] = {}

    host_m = re.search(r"Host\s+Name\s*:\s*(\S+)", raw, re.IGNORECASE)
    if host_m:
        data["hostname"] = host_m.group(1).strip()

    uptime_m = re.search(r"Uptime\s*:\s*(.+)", raw, re.IGNORECASE)
    if uptime_m:
        data["uptime"] = uptime_m.group(1).strip()

    cpu_idle = re.search(r"CPU\s+Idle\s*:\s*([\d.]+)\s*%", raw, re.IGNORECASE)
    if cpu_idle:
        idle = float(cpu_idle.group(1))
        data["cpu_idle_pct"] = round(idle, 2)
        data["cpu_utilization_pct"] = round(max(0.0, min(100.0, 100.0 - idle)), 2)

    # Memory Total/Used (in K or M)
    mem_total = re.search(r"Memory\s+Total\s*:\s*([\d.]+)\s*([KMG]?)", raw, re.IGNORECASE)
    mem_used = re.search(r"^\s*(?:Memory\s+)?Used\s*:\s*([\d.]+)\s*([KMG]?)", raw, re.IGNORECASE | re.MULTILINE)
    if mem_total and mem_used:
        total_mb = _to_mb(float(mem_total.group(1)), mem_total.group(2))
        used_mb = _to_mb(float(mem_used.group(1)), mem_used.group(2))
        data["memory_total_mb"] = round(total_mb, 1)
        data["memory_used_mb"] = round(used_mb, 1)
        if total_mb > 0:
            data["memory_utilization_pct"] = round((used_mb / total_mb) * 100.0, 2)

    # Disk/common or Disk/active|inactive|logging percentage in show status
    disk_lines = []
    for line in raw.splitlines():
        m = re.search(
            r"^(?:Disk/)?(Common|Active|Inactive|Boot|Logging)\s+([\d.]+[KMG]?)\s+([\d.]+[KMG]?)\s+([\d.]+[KMG]?)\s+\(([\d.]+)%\)",
            line.strip(),
            re.IGNORECASE,
        )
        if m:
            pct = float(m.group(5))
            part_name = m.group(1)
            disk_lines.append({
                "partition": part_name,
                "total": m.group(2),
                "free": m.group(3),
                "used": m.group(4),
                "used_pct": pct,
            })
            if part_name.lower() in ("common", "logging"):
                data["disk_utilization_pct"] = pct
    if disk_lines:
        data["disk_partitions"] = disk_lines

    return data


def parse_show_hardware(raw: str) -> Dict[str, Any]:
    """Parse 'show hardware' output."""
    from backend.collectors.disk_health import parse_diskusage_common

    data: Dict[str, Any] = {}
    platform_m = re.search(r"(?:Platform\s+Type|HW\s+Platform)\s*:\s*(.+)", raw, re.IGNORECASE)
    if platform_m:
        data["hardware_model"] = platform_m.group(1).strip()

    cpu_m = re.search(r"(?:Processor\s+Count|CPU\s+Count|Number\s+of\s+CPUs|Processors)\s*:\s*(\d+)", raw, re.IGNORECASE)
    if cpu_m:
        data["cpu_count"] = int(cpu_m.group(1))

    mem_m = re.search(r"Memory\s*:\s*([\d.]+)\s*([KMG]?B?)", raw, re.IGNORECASE)
    if mem_m and "memory_total_mb" not in data:
        unit = mem_m.group(2)[:1] if mem_m.group(2) else "M"
        data["memory_total_mb"] = round(_to_mb(float(mem_m.group(1)), unit), 1)

    # Extract filesystem table from show hardware if present (e.g. /dev/sda6 ... 33% /common)
    disk_data = parse_diskusage_common(raw)
    if disk_data:
        data.update(disk_data)

    return data


def _to_mb(value: float, unit: str) -> float:
    u = (unit or "K").upper()
    if u == "G":
        return value * 1024.0
    if u == "M":
        return value
    if u == "K":
        return value / 1024.0
    return value
