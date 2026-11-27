"""Read-only CUCM cluster node tools."""

from typing import Any, Dict, Optional

from backend.integrations.cucm_axl.client import CUCMToolkitAXLClient
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry, ToolSpec


def register_cluster_tools(registry: AXLToolRegistry, client: Optional[CUCMToolkitAXLClient] = None) -> None:
    def _client(params: Dict[str, Any]) -> CUCMToolkitAXLClient:
        return client or CUCMToolkitAXLClient.from_params(params)

    def handle_get_cluster_nodes(params: Dict[str, Any]) -> Dict[str, Any]:
        c = _client(params)
        version = c.axl.get_cucm_version()
        ev = c.sql.execute_template_with_evidence("cluster_nodes")
        return {
            "cucm_version": version,
            "count": ev["count"],
            "rows": ev["rows"],
            "template_id": ev["template_id"],
            "endpoint": ev["endpoint"],
            "axl_version": ev["axl_version"],
            "authentication_status": ev["authentication_status"],
            "query_status": ev["query_status"],
            "query_outcome": ev["query_outcome"],
        }

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
