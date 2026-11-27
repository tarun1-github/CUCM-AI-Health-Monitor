"""Toolkit loader for CUCM AXL versioned schemas and tool registry."""

from typing import Any, Dict, Optional

from backend.integrations.cucm_axl.schema_manager import AXLSchemaManager
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry, get_axl_tool_registry


def load_axl_toolkit(axl_version: Optional[str] = None) -> Dict[str, Any]:
    """Load CUCM AXL schema metadata and initialize the read-only tool registry."""
    schema_mgr = AXLSchemaManager(axl_version=axl_version)
    registry: AXLToolRegistry = get_axl_tool_registry()
    return {
        "schema": schema_mgr.info(),
        "tool_count": len(registry.list_tools()),
        "tools": [t.name for t in registry.list_tools()],
    }
