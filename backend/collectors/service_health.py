"""Parser for CUCM 'utils service list' output."""

import re
from typing import List, Set

from backend.models.health import HealthStatus, ServiceCriticality, ServiceStatusItem

_SERVICE_LINE_RE = re.compile(r"^(.+?)\s*\[\s*([A-Z _-]+)\s*\](?:\s+(.*))?$")


def parse_service_list(
    raw: str,
    critical_services: Set[str] | None = None,
    important_services: Set[str] | None = None,
    optional_services: Set[str] | None = None,
) -> List[ServiceStatusItem]:
    """Parse 'utils service list' output into ServiceStatusItem records."""
    critical_lower = {s.lower() for s in (critical_services or set())}
    important_lower = {s.lower() for s in (important_services or set())}
    optional_lower = {s.lower() for s in (optional_services or set())}
    items: List[ServiceStatusItem] = []

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("-") or stripped.lower().startswith("requesting"):
            continue
        m = _SERVICE_LINE_RE.match(stripped)
        if not m:
            continue

        name = m.group(1).strip()
        status = m.group(2).strip().upper()
        name_l = name.lower()
        is_crit = name_l in critical_lower
        if is_crit:
            criticality = ServiceCriticality.CRITICAL
        elif name_l in important_lower:
            criticality = ServiceCriticality.IMPORTANT
        elif name_l in optional_lower:
            criticality = ServiceCriticality.OPTIONAL
        else:
            criticality = ServiceCriticality.UNKNOWN

        if status == "STARTED":
            health = HealthStatus.HEALTHY
        elif status in ("NOT ACTIVATED", "NOT_ACTIVATED"):
            health = HealthStatus.WARNING if is_crit else HealthStatus.HEALTHY
        elif status in ("STOPPED", "DOWN", "FAILED"):
            if is_crit:
                health = HealthStatus.CRITICAL
            elif criticality == ServiceCriticality.OPTIONAL:
                health = HealthStatus.WARNING
            else:
                health = HealthStatus.WARNING
        else:
            health = HealthStatus.UNKNOWN

        desc = (
            "Critical CUCM service"
            if criticality == ServiceCriticality.CRITICAL
            else f"{criticality.value.title()} CUCM service"
            if criticality != ServiceCriticality.UNKNOWN
            else "CUCM platform/application service"
        )
        items.append(
            ServiceStatusItem(
                name=name,
                status=status,
                health=health,
                is_critical=is_crit,
                criticality=criticality,
                description=desc,
            )
        )

    return items
