"""Read-only CUCM cluster node tools."""

from typing import Any, Dict, Optional

from backend.integrations.cucm_axl.client import CUCMToolkitAXLClient
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry, ToolSpec


def register_cluster_tools(registry: AXLToolRegistry, client: Optional[CUCMToolkitAXLClient] = None) -> None:
    def _client() -> CUCMToolkitAXLClient:
        return client or CUCMToolkitAXLClient()

    def handle_get_cluster_nodes(params: Dict[str, Any]) -> Dict[str, Any]:
        c = _client()
        version = c.axl.get_cucm_version()
        rows = c.sql.execute_template("cluster_nodes")
        return {"cucm_version": version, "count": len(rows), "rows": rows}

    registry.register(
        ToolSpec(
            name="cucm_get_cluster_nodes",
            description="Retrieve CUCM cluster Publisher and Subscriber node details and CUCM version.",
            input_schema={},
            output_schema={"cucm_version": "string", "count": "int", "rows": "list"},
            source_api="AXL",
        ),
        handle_get_cluster_nodes,
    )
