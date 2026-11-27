"""Read-only CUCM RISPort70 real-time phone registration tools."""

import ipaddress
from typing import Any, Dict, Optional

from backend.integrations.cucm_axl.client import CUCMToolkitAXLClient
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry, ToolSpec


def _is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return False


def register_registration_tools(registry: AXLToolRegistry, client: Optional[CUCMToolkitAXLClient] = None) -> None:
    def _client(params: Dict[str, Any]) -> CUCMToolkitAXLClient:
        return client or CUCMToolkitAXLClient.from_params(params)

    def handle_get_phone_registration(params: Dict[str, Any]) -> Dict[str, Any]:
        device_name = str(params.get("device_name", "*") or "*").strip()
        status_filter = str(params.get("status", "Any") or "Any").strip()
        node_name = str(params.get("node_name", "") or "").strip()
        limit = int(params.get("limit", 200))

        ris = _client(params).risport.select_cm_device(
            device_name=device_name,
            device_class="Phone",
            status=status_filter,
            node_name=node_name,
            max_devices=min(max(limit, 1), 1000),
        )
        devices = ris["devices"]
        # Only post-filter by node_name when it's a CUCM node hostname (not an IP),
        # because CUCM returns CmNodes/Name as the node hostname (e.g. UCM15-HQ-PUB).
        if node_name and not _is_ip_address(node_name):
            devices = [d for d in devices if (d.get("node_name") or "").lower() == node_name.lower()]

        registered_count = sum(1 for d in devices if (d.get("status") or "").lower() == "registered")
        unregistered_count = sum(1 for d in devices if (d.get("status") or "").lower() != "registered")
        query_outcome = "SUCCESS_WITH_RECORDS" if devices else "SUCCESS_WITH_ZERO_RECORDS"

        return {
            "success": True,
            "source": "RISPort70",
            "target_ip": ris.get("target_ip"),
            "endpoint": ris.get("endpoint"),
            "operation": ris.get("operation", "selectCmDevice"),
            "authentication_status": ris.get("authentication_status", "SUCCESS"),
            "query_status": query_outcome,
            "query_outcome": query_outcome,
            "total_devices_found": ris.get("total_devices_found", len(devices)),
            "total_found": len(devices),
            "registered_count": registered_count,
            "unregistered_count": unregistered_count,
            "node_filter": node_name or None,
            "status_filter": status_filter,
            "state_info": ris.get("state_info"),
            "duration_ms": ris.get("duration_ms"),
            "collected_at_utc": ris.get("collected_at_utc"),
            "collected_at_ist": ris.get("collected_at_ist"),
            "rows": devices[:limit],
        }

    def handle_list_unregistered_phones(params: Dict[str, Any]) -> Dict[str, Any]:
        limit = int(params.get("limit", 200))
        ris = _client(params).risport.select_cm_device(
            device_name="*",
            device_class="Phone",
            status="Any",
            max_devices=1000,
        )
        all_devs = ris["devices"]
        unreg = [d for d in all_devs if (d.get("status") or "").lower() != "registered"]
        query_outcome = "SUCCESS_WITH_RECORDS" if unreg else "SUCCESS_WITH_ZERO_RECORDS"
        return {
            "success": True,
            "source": "RISPort70",
            "target_ip": ris.get("target_ip"),
            "endpoint": ris.get("endpoint"),
            "operation": ris.get("operation", "selectCmDevice"),
            "authentication_status": ris.get("authentication_status", "SUCCESS"),
            "query_status": query_outcome,
            "query_outcome": query_outcome,
            "total_phones_seen": len(all_devs),
            "unregistered_count": len(unreg),
            "collected_at_utc": ris.get("collected_at_utc"),
            "collected_at_ist": ris.get("collected_at_ist"),
            "rows": unreg[:limit],
        }

    def handle_list_phones_by_node(params: Dict[str, Any]) -> Dict[str, Any]:
        node_name = str(params.get("node_name", "")).strip()
        if not node_name:
            raise ValueError("Parameter 'node_name' is required.")
        limit = int(params.get("limit", 200))
        ris = _client(params).risport.select_cm_device(
            device_name="*",
            device_class="Phone",
            status="Registered",
            node_name=node_name,
            max_devices=1000,
        )
        devs = ris["devices"]
        if not _is_ip_address(node_name):
            devs = [d for d in devs if (d.get("node_name") or "").lower() == node_name.lower()]
        query_outcome = "SUCCESS_WITH_RECORDS" if devs else "SUCCESS_WITH_ZERO_RECORDS"
        return {
            "success": True,
            "source": "RISPort70",
            "target_ip": ris.get("target_ip"),
            "endpoint": ris.get("endpoint"),
            "operation": ris.get("operation", "selectCmDevice"),
            "authentication_status": ris.get("authentication_status", "SUCCESS"),
            "query_status": query_outcome,
            "query_outcome": query_outcome,
            "node_name": node_name,
            "registered_count": len(devs),
            "collected_at_utc": ris.get("collected_at_utc"),
            "collected_at_ist": ris.get("collected_at_ist"),
            "rows": devs[:limit],
        }

    registry.register(
        ToolSpec(
            name="cucm_get_phone_registration",
            description="Query real-time phone registration status, counts (registered vs unregistered), IP, and node via RISPort70.",
            input_schema={"device_name": "string (default *)", "status": "Any|Registered|UnRegistered", "node_name": "string (optional)", "limit": "int (optional)"},
            output_schema={"total_found": "int", "registered_count": "int", "unregistered_count": "int", "rows": "list"},
            source_api="RISPort70",
        ),
        handle_get_phone_registration,
    )
    registry.register(
        ToolSpec(
            name="cucm_list_unregistered_phones",
            description="List all unregistered/rejected/unknown phones currently reported by RISPort70.",
            input_schema={"limit": "int (optional, default 200)"},
            output_schema={"total_phones_seen": "int", "unregistered_count": "int", "rows": "list"},
            source_api="RISPort70",
        ),
        handle_list_unregistered_phones,
    )
    registry.register(
        ToolSpec(
            name="cucm_list_phones_by_node",
            description="List all phones currently registered to a specific CUCM node (e.g., UCM15-HQ-SUB1).",
            input_schema={"node_name": "string (required)", "limit": "int (optional, default 200)"},
            output_schema={"node_name": "string", "registered_count": "int", "rows": "list"},
            source_api="RISPort70",
        ),
        handle_list_phones_by_node,
    )
