"""Parsers for CPU and Memory CUCM CLI outputs."""

import re
from typing import Any, Dict


def parse_process_load(raw: str) -> Dict[str, Any]:
    """Parse 'show process load' output."""
    data: Dict[str, Any] = {}

    load_m = re.search(
        r"load\s+average:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)",
        raw,
        re.IGNORECASE,
    )
    if load_m:
        data["load_average_1m"] = float(load_m.group(1))
        data["load_average_5m"] = float(load_m.group(2))
        data["load_average_15m"] = float(load_m.group(3))

    cpu_idle = re.search(r"([\d.]+)\s*%?\s*id(?:le)?", raw, re.IGNORECASE)
    if cpu_idle:
        idle = float(cpu_idle.group(1))
        data["cpu_idle_pct"] = round(idle, 2)
        data["cpu_utilization_pct"] = round(max(0.0, min(100.0, 100.0 - idle)), 2)

    mem_m = re.search(
        r"(?:MiB\s+)?Mem\s*:\s*([\d.]+)\s+total,\s*([\d.]+)\s+free,\s*([\d.]+)\s+used",
        raw,
        re.IGNORECASE,
    )
    if mem_m:
        total = float(mem_m.group(1))
        used = float(mem_m.group(3))
        data["memory_total_mb"] = round(total, 1)
        data["memory_used_mb"] = round(used, 1)
        if total > 0:
            data["memory_utilization_pct"] = round((used / total) * 100.0, 2)

    return data


def parse_perf_processor(raw: str) -> Dict[str, Any]:
    """Parse 'show perf query class Processor' output."""
    data: Dict[str, Any] = {}
    idle_m = re.search(
        r"(?:_Total\s*->\s*(?:Idle\s+Percentage|%\s*Idle\s+Time)|%\s*Idle\s+Time)\s*=\s*([\d.]+)",
        raw,
        re.IGNORECASE,
    )
    if idle_m:
        idle = float(idle_m.group(1))
        data["cpu_idle_pct"] = round(idle, 2)
        data["cpu_utilization_pct"] = round(max(0.0, min(100.0, 100.0 - idle)), 2)
        return data

    total_m = re.search(
        r"(?:_Total\s*->\s*%\s*(?:CPU|Processor)\s+Time|%\s*(?:CPU|Processor)\s+Time)\s*=\s*([\d.]+)",
        raw,
        re.IGNORECASE,
    )
    if total_m:
        data["cpu_utilization_pct"] = round(float(total_m.group(1)), 2)
    return data


def parse_perf_memory(raw: str) -> Dict[str, Any]:
    """Parse 'show perf query class Memory' output."""
    data: Dict[str, Any] = {}
    pct_m = re.search(r"%\s*Mem(?:ory)?\s+Used\s*=\s*([\d.]+)", raw, re.IGNORECASE)
    if pct_m:
        data["memory_utilization_pct"] = round(float(pct_m.group(1)), 2)

    total_m = re.search(r"Total\s+KBytes\s*=\s*([\d.]+)", raw, re.IGNORECASE)
    used_m = re.search(r"Used\s+KBytes\s*=\s*([\d.]+)", raw, re.IGNORECASE)
    if total_m and used_m:
        total_mb = float(total_m.group(1)) / 1024.0
        used_mb = float(used_m.group(1)) / 1024.0
        data["memory_total_mb"] = round(total_mb, 1)
        data["memory_used_mb"] = round(used_mb, 1)
        if total_mb > 0 and "memory_utilization_pct" not in data:
            data["memory_utilization_pct"] = round((used_mb / total_mb) * 100.0, 2)
    return data
