"""Central read-only CUCM AXL & RISPort70 Tool Registry."""

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: Dict[str, str] = Field(default_factory=dict)
    output_schema: Dict[str, str] = Field(default_factory=dict)
    auth_required: str = "CUCM AXL/RISPort70 Basic Auth"
    read_only: bool = True
    source_api: str = "AXL"  # AXL or RISPort70


class AXLToolRegistry:
    """Registry of approved read-only CUCM AXL and RISPort70 tools."""

    def __init__(self) -> None:
        self._specs: Dict[str, ToolSpec] = {}
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}

    def register(
        self,
        spec: ToolSpec,
        handler: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> None:
        if not spec.read_only:
            raise ValueError(f"Tool '{spec.name}' must be read_only=True.")
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler

    def list_tools(self) -> List[ToolSpec]:
        return list(self._specs.values())

    def get_spec(self, name: str) -> Optional[ToolSpec]:
        return self._specs.get(name)

    def execute(self, name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        spec = self._specs.get(name)
        handler = self._handlers.get(name)
        if spec is None or handler is None:
            return {
                "success": False,
                "tool": name,
                "error": f"Tool '{name}' is not registered in the read-only CUCM toolkit.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        try:
            result = handler(params or {})
            result.setdefault("success", True)
            result.setdefault("tool", name)
            result.setdefault("source", spec.source_api)
            result.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
            return result
        except Exception as exc:
            return {
                "success": False,
                "tool": name,
                "source": spec.source_api,
                "error": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }


_REGISTRY: Optional[AXLToolRegistry] = None


def get_axl_tool_registry() -> AXLToolRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = AXLToolRegistry()
        from backend.tools import register_all_cucm_tools
        register_all_cucm_tools(_REGISTRY)
    return _REGISTRY
