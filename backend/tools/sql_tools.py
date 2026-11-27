"""Read-only approved SQL template execution tool."""

from typing import Any, Dict, Optional

from backend.integrations.cucm_axl.client import CUCMToolkitAXLClient
from backend.integrations.cucm_axl.sql_executor import APPROVED_SQL_TEMPLATES
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry, ToolSpec


def register_sql_tools(registry: AXLToolRegistry, client: Optional[CUCMToolkitAXLClient] = None) -> None:
    def _client(params: Dict[str, Any]) -> CUCMToolkitAXLClient:
        return client or CUCMToolkitAXLClient.from_params(params)

    def handle_approved_sql(params: Dict[str, Any]) -> Dict[str, Any]:
        template = str(params.get("template_name", "")).strip()
        if not template:
            raise ValueError(f"Parameter 'template_name' is required. Allowed: {sorted(APPROVED_SQL_TEMPLATES.keys())}")
        sql_params = params.get("params") or {}
        limit = int(params.get("limit", 200))
        ev = _client(params).sql.execute_template_with_evidence(template, params=sql_params, limit=limit)
        return {"template_name": template, **ev}

    registry.register(
        ToolSpec(
            name="cucm_approved_sql_query",
            description=f"Execute a pre-approved read-only Informix SELECT template. Allowed templates: {', '.join(sorted(APPROVED_SQL_TEMPLATES))}.",
            input_schema={"template_name": "string (required)", "params": "dict (optional)", "limit": "int (optional)"},
            output_schema={"count": "int", "rows": "list"},
            source_api="AXL SQL",
        ),
        handle_approved_sql,
    )
