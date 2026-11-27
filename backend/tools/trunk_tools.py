"""Read-only CUCM SIP Trunk tools."""

from typing import Any, Dict, Optional

from backend.integrations.cucm_axl.client import CUCMToolkitAXLClient
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry, ToolSpec


def register_trunk_tools(registry: AXLToolRegistry, client: Optional[CUCMToolkitAXLClient] = None) -> None:
    def _client() -> CUCMToolkitAXLClient:
        return client or CUCMToolkitAXLClient()

    def handle_list_sip_trunks(params: Dict[str, Any]) -> Dict[str, Any]:
        limit = int(params.get("limit", 200))
        rows = _client().sql.execute_template("sip_trunks", limit=limit)
        return {"count": len(rows), "rows": rows}

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
