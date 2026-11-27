"""Parsers for CUCM network and diagnostic CLI outputs."""

import re
from typing import Any, Dict, List


def parse_network_eth0(raw: str) -> Dict[str, Any]:
    """Parse 'show network eth0' output."""
    data: Dict[str, Any] = {"interface_name": "eth0"}

    patterns = {
        "ip_address": r"IP\s+Address\s*:\s*([\d.]+)",
        "subnet_mask": r"IP\s+Mask\s*:\s*([\d.]+)",
        "gateway": r"Gateway\s*:\s*([\d.]+)",
        "dns_primary": r"(?:Primary\s+DNS|Primary)\s*:\s*([\d.]+)",
        "dns_secondary": r"(?:Secondary\s+DNS|Secondary)\s*:\s*([\d.]+)",
        "link_status": r"Link\s+Detected\s*:\s*(\S+)",
        "mtu": r"MTU\s*:\s*(\d+)",
    }

    for key, pattern in patterns.items():
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            val: Any = m.group(1).strip()
            if key == "mtu":
                val = int(val)
            data[key] = val

    return data


def parse_diagnose_test(raw: str) -> List[Dict[str, str]]:
    """Parse 'utils diagnose test' output into test-status records."""
    by_test: Dict[str, str] = {}
    for line in raw.splitlines():
        m = re.search(r"^\s*(?:test|skip)\s*-\s*([^\s:]+)\s*:\s*(.+)$", line, re.IGNORECASE)
        if m:
            test_name = m.group(1).strip()
            result = m.group(2).strip()
            if re.search(r"\d+\s+second\(s\)\s+left", result, re.IGNORECASE):
                continue
            by_test[test_name] = result
    return [{"test": k, "result": v} for k, v in by_test.items()]
