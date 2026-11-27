"""Read-only CUCM phone and device pool tools."""

from typing import Any, Dict, Optional

from backend.integrations.cucm_axl.client import CUCMToolkitAXLClient
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry, ToolSpec


def register_phone_tools(registry: AXLToolRegistry, client: Optional[CUCMToolkitAXLClient] = None) -> None:
    def _client() -> CUCMToolkitAXLClient:
        return client or CUCMToolkitAXLClient()

    def handle_get_phone(params: Dict[str, Any]) -> Dict[str, Any]:
        device_name = str(params.get("device_name", "")).strip()
        if not device_name:
            raise ValueError("Parameter 'device_name' is required.")
        c = _client()
        phone = c.soap.get_phone(device_name)
        reg = None
        try:
            ris = c.risport.select_cm_device(device_name=device_name)
            if ris["devices"]:
                reg = ris["devices"][0]
        except Exception:
            pass
        if not phone and not reg:
            return {"found": False, "device_name": device_name, "rows": [], "summary": f"Phone '{device_name}' not found."}
        row = {
            "name": (phone or {}).get("name") or (reg or {}).get("name") or device_name,
            "description": (phone or {}).get("description") or (reg or {}).get("description"),
            "model": (phone or {}).get("model") or (reg or {}).get("model"),
            "device_pool": (phone or {}).get("devicePoolName"),
            "css": (phone or {}).get("callingSearchSpaceName"),
            "location": (phone or {}).get("locationName"),
            "registration_status": (reg or {}).get("status", "Unknown"),
            "ip_address": (reg or {}).get("ip_address"),
            "node_name": (reg or {}).get("node_name"),
            "lines": (phone or {}).get("lines", []),
        }
        return {"found": True, "phone": row, "rows": [row]}

    def handle_find_phone_by_dn(params: Dict[str, Any]) -> Dict[str, Any]:
        pattern = str(params.get("pattern", "")).strip()
        if not pattern:
            raise ValueError("Parameter 'pattern' (directory number) is required.")
        rows = _client().sql.execute_template("phones_by_dn", {"pattern": pattern})
        return {"pattern": pattern, "count": len(rows), "rows": rows}

    def handle_list_device_pools(params: Dict[str, Any]) -> Dict[str, Any]:
        limit = int(params.get("limit", 200))
        rows = _client().sql.execute_template("device_pools", limit=limit)
        return {"count": len(rows), "rows": rows}

    registry.register(
        ToolSpec(
            name="cucm_get_phone",
            description="Get detailed configuration and live registration status of a specific CUCM phone by device name (e.g. SEP...).",
            input_schema={"device_name": "string (required)"},
            output_schema={"found": "bool", "phone": "dict", "rows": "list"},
            source_api="AXL + RISPort70",
        ),
        handle_get_phone,
    )
    registry.register(
        ToolSpec(
            name="cucm_find_phone_by_dn",
            description="Find CUCM phone(s) associated with a specific directory number / extension.",
            input_schema={"pattern": "string (required)"},
            output_schema={"count": "int", "rows": "list"},
            source_api="AXL SQL",
        ),
        handle_find_phone_by_dn,
    )
    registry.register(
        ToolSpec(
            name="cucm_list_device_pools",
            description="List all configured CUCM device pools with CallManager group, date/time group, and region.",
            input_schema={"limit": "int (optional, default 200)"},
            output_schema={"count": "int", "rows": "list"},
            source_api="AXL SQL",
        ),
        handle_list_device_pools,
    )
