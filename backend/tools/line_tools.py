"""Read-only CUCM directory number / line tools."""

from typing import Any, Dict, Optional

from backend.integrations.cucm_axl.client import CUCMToolkitAXLClient
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry, ToolSpec


def register_line_tools(registry: AXLToolRegistry, client: Optional[CUCMToolkitAXLClient] = None) -> None:
    def _client(params: Dict[str, Any]) -> CUCMToolkitAXLClient:
        return client or CUCMToolkitAXLClient.from_params(params)

    def handle_get_line(params: Dict[str, Any]) -> Dict[str, Any]:
        pattern = str(params.get("pattern", "")).strip()
        if not pattern:
            raise ValueError("Parameter 'pattern' is required.")
        rows = _client(params).sql.execute_template("line_by_pattern", {"pattern": pattern})
        return {"pattern": pattern, "count": len(rows), "rows": rows}

    registry.register(
        ToolSpec(
            name="cucm_get_line",
            description="Retrieve directory number configuration, route partition, alerting name, and CSS.",
            input_schema={"pattern": "string (required)"},
            output_schema={"count": "int", "rows": "list"},
            source_api="AXL SQL",
        ),
        handle_get_line,
    )
