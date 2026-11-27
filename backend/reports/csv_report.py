"""CSV health report exporter."""

import csv
from pathlib import Path

from backend.models.health import HealthSnapshot


def write_csv_report(snapshot: HealthSnapshot, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sys = snapshot.system
    cls = snapshot.classification

    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Section", "Metric", "Value", "Health"])
        writer.writerow(["Device", "Host", snapshot.device_host, cls.overall_health.value])
        writer.writerow(["Device", "Hostname", sys.hostname or "UNKNOWN", ""])
        writer.writerow(["Device", "CUCM Version", sys.cucm_version or "UNKNOWN", ""])
        writer.writerow(["Device", "Role", snapshot.device_role, ""])
        writer.writerow(["System", "CPU Utilization %", sys.cpu_utilization_pct if sys.cpu_utilization_pct is not None else "UNKNOWN", cls.cpu_health.value])
        writer.writerow(["System", "Memory Utilization %", sys.memory_utilization_pct if sys.memory_utilization_pct is not None else "UNKNOWN", cls.memory_health.value])
        writer.writerow(["System", "Disk Utilization %", sys.disk_utilization_pct if sys.disk_utilization_pct is not None else "UNKNOWN", cls.disk_health.value])
        writer.writerow(["Network", "IP Address", snapshot.network.ip_address or "UNKNOWN", cls.network_health.value])

        for svc in snapshot.services:
            writer.writerow(["Service", svc.name, svc.status, svc.health.value])

    return output_path
