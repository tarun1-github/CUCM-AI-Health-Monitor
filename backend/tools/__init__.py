"""Read-only CUCM AXL and RISPort70 tool registrations."""

from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry
from backend.tools.cluster_tools import register_cluster_tools
from backend.tools.cti_tools import register_cti_tools
from backend.tools.line_tools import register_line_tools
from backend.tools.live_health_tools import register_live_health_tools
from backend.tools.phone_tools import register_phone_tools
from backend.tools.registration_tools import register_registration_tools
from backend.tools.sql_tools import register_sql_tools
from backend.tools.trunk_tools import register_trunk_tools


def register_all_cucm_tools(registry: AXLToolRegistry) -> None:
    register_phone_tools(registry)
    register_line_tools(registry)
    register_cti_tools(registry)
    register_trunk_tools(registry)
    register_cluster_tools(registry)
    register_registration_tools(registry)
    register_sql_tools(registry)
    register_live_health_tools(registry)
