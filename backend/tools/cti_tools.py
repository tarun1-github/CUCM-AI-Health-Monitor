"""Read-only CUCM CTI Route Point tools."""

from typing import Any, Dict, Optional

from backend.integrations.cucm_axl.client import CUCMToolkitAXLClient
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry, ToolSpec


def register_cti_tools(registry: AXLToolRegistry, client: Optional[CUCMToolkitAXLClient] = None) -> None:
    def _client(params: Dict[str, Any]) -> CUCMToolkitAXLClient:
        return client or CUCMToolkitAXLClient.from_params(params)

    def handle_list_cti_route_points(params: Dict[str, Any]) -> Dict[str, Any]:
        limit = int(params.get("limit", 200))
        ev = _client(params).sql.execute_template_with_evidence("cti_route_points", limit=limit)
        rows = ev["rows"]
        # Group multiple DNs per CTI Route Point if any
        by_name: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            name = r.get("name") or "Unknown"
            if name not in by_name:
                by_name[name] = {
                    "name": name,
                    "description": r.get("description") or "",
                    "device_pool": r.get("device_pool") or "",
                    "location": r.get("location") or "",
                    "directory_numbers": [],
                }
            dn = r.get("directory_number")
            if dn and dn not in by_name[name]["directory_numbers"]:
                by_name[name]["directory_numbers"].append(dn)

        formatted = [
            {
                "name": v["name"],
                "description": v["description"],
                "device_pool": v["device_pool"],
                "location": v["location"],
                "directory_numbers": ", ".join(v["directory_numbers"]) or "-",
            }
            for v in by_name.values()
        ]
        return {"count": len(formatted), "rows": formatted}

    registry.register(
        ToolSpec(
            name="cucm_list_cti_route_points",
            description="Count and list all configured CUCM CTI Route Points with description, device pool, location, and directory numbers.",
            input_schema={"limit": "int (optional, default 200)"},
            output_schema={"count": "int", "rows": "list"},
            source_api="AXL SQL",
        ),
        handle_list_cti_route_points,
    )
