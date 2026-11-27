"""Version-aware CUCM AXL schema manager (reads AXLAPI.wsdl / AXLSoap.xsd when available)."""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Set

from backend.utils.config_loader import PROJECT_ROOT, get_settings

# Known CUCM AXL schema locations (local project first, then sibling reference toolkit if present)
_CANDIDATE_SCHEMA_ROOTS = [
    PROJECT_ROOT / "axlsqltoolkit" / "schema",
    PROJECT_ROOT.parent / "EnterpriseAgenticAIforVoiceopsAssistant" / "axlsqltoolkit" / "schema",
]


class AXLSchemaManager:
    """Manages versioned CUCM AXL WSDL/XSD schemas and validates operation compatibility."""

    def __init__(self, axl_version: Optional[str] = None, schema_root: Optional[Path] = None):
        settings = get_settings()
        self.axl_version = axl_version or settings.cucm_axl_version or "15.0"
        self.schema_root = schema_root or self._discover_schema_root()
        self._operations_cache: Optional[Set[str]] = None

    @staticmethod
    def _discover_schema_root() -> Optional[Path]:
        for candidate in _CANDIDATE_SCHEMA_ROOTS:
            if candidate.exists() and candidate.is_dir():
                return candidate
        return None

    def available_versions(self) -> List[str]:
        if not self.schema_root or not self.schema_root.exists():
            return []
        return sorted(
            p.name for p in self.schema_root.iterdir() if p.is_dir() and (p / "AXLAPI.wsdl").exists()
        )

    def get_schema_dir(self, version: Optional[str] = None) -> Optional[Path]:
        ver = version or self.axl_version
        if not self.schema_root:
            return None
        target = self.schema_root / ver
        if target.exists() and (target / "AXLAPI.wsdl").exists():
            return target
        return None

    def get_wsdl_path(self, version: Optional[str] = None) -> Optional[Path]:
        d = self.get_schema_dir(version)
        return (d / "AXLAPI.wsdl") if d and (d / "AXLAPI.wsdl").exists() else None

    def get_supported_operations(self) -> Set[str]:
        """Parse AXLAPI.wsdl portType operations without requiring external zeep/lxml dependencies."""
        if self._operations_cache is not None:
            return self._operations_cache

        wsdl = self.get_wsdl_path()
        if not wsdl:
            # Known core read-only AXL 15.0 operations fallback when WSDL files are not bundled
            self._operations_cache = {
                "getCCMVersion",
                "executeSQLQuery",
                "getPhone",
                "listPhone",
                "getLine",
                "listLine",
                "getCtiRoutePoint",
                "listCtiRoutePoint",
                "getSipTrunk",
                "listSipTrunk",
                "getDevicePool",
                "listDevicePool",
                "getProcessNode",
                "listProcessNode",
            }
            return self._operations_cache

        ops: Set[str] = set()
        try:
            tree = ET.parse(wsdl)
            root = tree.getroot()
            for elem in root.iter():
                if elem.tag.endswith("operation") and "name" in elem.attrib:
                    ops.add(elem.attrib["name"])
        except Exception:
            pass
        self._operations_cache = ops
        return ops

    def is_operation_supported(self, operation: str) -> bool:
        return operation in self.get_supported_operations()

    def info(self) -> Dict[str, object]:
        wsdl = self.get_wsdl_path()
        ops = self.get_supported_operations()
        return {
            "axl_version": self.axl_version,
            "schema_root": str(self.schema_root) if self.schema_root else None,
            "wsdl_found": bool(wsdl),
            "wsdl_path": str(wsdl) if wsdl else None,
            "available_versions": self.available_versions(),
            "operation_count": len(ops),
        }
