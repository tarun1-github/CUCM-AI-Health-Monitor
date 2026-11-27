"""Read-only CUCM SIP Trunk tools."""

from typing import Any, Dict, Optional

from backend.integrations.cucm_axl.client import CUCMToolkitAXLClient
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry, ToolSpec


def register_trunk_tools(registry: AXLToolRegistry, client: Optional[CUCMToolkitAXLClient] = None) -> None:
    def _client(params: Dict[str, Any]) -> CUCMToolkitAXLClient:
        return client or CUCMToolkitAXLClient.from_params(params)

    def handle_list_sip_trunks(params: Dict[str, Any]) -> Dict[str, Any]:
        limit = int(params.get("limit", 200))
        ev = _client(params).sql.execute_template_with_evidence("sip_trunks", limit=limit)
        return {
            "count": ev["count"],
            "rows": ev["rows"],
            "template_id": ev["template_id"],
            "endpoint": ev["endpoint"],
            "axl_version": ev["axl_version"],
            "authentication_status": ev["authentication_status"],
            "query_status": ev["query_status"],
            "query_outcome": ev["query_outcome"],
            "duration_ms": ev["duration_ms"],
        }

    registry.register(
        ToolSpec(
            name="cucm_list_sip_trunks",
            description="List all configured SIP Trunks with description, device pool, SIP profile, and CSS.",
            input_schema={"limit": "int (optional, default 200)"},
            output_schema={"count": "int", "rows": "list"},
            source_api="AXL SQL",
        ),
        handle_list_sip_trunks,
    )
