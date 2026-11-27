"""Parser for CUCM disk utilization CLI outputs."""

import re
from typing import Any, Dict, List


def parse_diskusage_common(raw: str) -> Dict[str, Any]:
    """Parse 'show diskusage common' output."""
    data: Dict[str, Any] = {}
    partitions: List[Dict[str, Any]] = []

    # Typical df-like line: /dev/sda6  76G  52G  21G  72% /common
    # Or with fstype (show hardware): /dev/sda6  ext4  66G  21G  43G  33% /common
    for line in raw.splitlines():
        m = re.search(
            r"^(\S+)(?:\s+[a-zA-Z0-9._-]+)?\s+([\d.]+[KMGTP]?)\s+([\d.]+[KMGTP]?)\s+([\d.]+[KMGTP]?)\s+([\d.]+)%\s+(/\S*)",
            line.strip(),
        )
        if m:
            pct = float(m.group(5))
            partitions.append({
                "filesystem": m.group(1),
                "total": m.group(2),
                "used": m.group(3),
                "available": m.group(4),
                "used_pct": pct,
                "mount": m.group(6),
            })
            if "/common" in m.group(6):
                data["disk_utilization_pct"] = pct
                data["disk_total_gb"] = _to_gb(m.group(2))
                data["disk_used_gb"] = _to_gb(m.group(3))

    if partitions:
        data["disk_partitions"] = partitions
        if "disk_utilization_pct" not in data:
            data["disk_utilization_pct"] = max(p["used_pct"] for p in partitions)

    return data


def _to_gb(size_str: str) -> float:
    m = re.match(r"([\d.]+)\s*([KMGTP]?)", size_str.strip(), re.IGNORECASE)
    if not m:
        return 0.0
    val = float(m.group(1))
    unit = m.group(2).upper()
    if unit == "T":
        return round(val * 1024.0, 2)
    if unit == "G":
        return round(val, 2)
    if unit == "M":
        return round(val / 1024.0, 2)
    if unit == "K":
        return round(val / (1024.0 * 1024.0), 2)
    return round(val, 2)
