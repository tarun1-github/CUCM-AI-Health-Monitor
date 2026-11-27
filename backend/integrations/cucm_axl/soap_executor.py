"""Structured read-only AXL SOAP operation executor."""

import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

from backend.connectivity.axl_client import CUCMAXLClient
from backend.connectivity.exceptions import CUCMAXLSoapFaultError


class AXLSoapExecutor:
    """Executes read-only structured AXL SOAP operations (getPhone, listPhone, listDevicePool, etc.)."""

    READ_ONLY_OPERATIONS = frozenset({
        "getCCMVersion",
        "getPhone",
        "listPhone",
        "getLine",
        "listLine",
        "listCtiRoutePoint",
        "getCtiRoutePoint",
        "listSipTrunk",
        "getSipTrunk",
        "listDevicePool",
        "listProcessNode",
    })

    def __init__(self, axl_client: Optional[CUCMAXLClient] = None):
        self.client = axl_client or CUCMAXLClient()

    def execute_operation(self, operation: str, body_xml: str = "") -> ET.Element:
        if operation not in self.READ_ONLY_OPERATIONS:
            raise CUCMAXLSoapFaultError(f"AXL operation '{operation}' is not in the read-only allowlist.")
        return self.client._post_soap(operation, body_xml)

    def get_ccm_version(self) -> Optional[str]:
        root = self.execute_operation("getCCMVersion")
        for elem in root.iter():
            if elem.tag.split("}")[-1] == "version" and elem.text and elem.text.strip():
                return elem.text.strip()
        return None

    def get_phone(self, device_name: str) -> Optional[Dict[str, Any]]:
        clean = escape(device_name.strip())
        try:
            root = self.execute_operation("getPhone", f"<name>{clean}</name>")
        except CUCMAXLSoapFaultError as exc:
            if "not found" in str(exc).lower() or "could not find" in str(exc).lower():
                return None
            raise

        phone_elem = next((e for e in root.iter() if e.tag.split("}")[-1] == "phone"), None)
        if phone_elem is None:
            return None
        return self._element_to_dict(phone_elem)

    def list_phones(self, name_pattern: str = "%", first: int = 100, skip: int = 0) -> List[Dict[str, Any]]:
        body = (
            f"<searchCriteria><name>{escape(name_pattern)}</name></searchCriteria>"
            "<returnedTags>"
            "<name/><description/><model/><devicePoolName/><callingSearchSpaceName/><locationName/>"
            "</returnedTags>"
            f"<skip>{max(0, int(skip))}</skip>"
            f"<first>{max(1, min(int(first), 500))}</first>"
        )
        root = self.execute_operation("listPhone", body)
        return self._extract_list_items(root, "phone")

    def list_cti_route_points(self, name_pattern: str = "%", first: int = 200, skip: int = 0) -> List[Dict[str, Any]]:
        body = (
            f"<searchCriteria><name>{escape(name_pattern)}</name></searchCriteria>"
            "<returnedTags>"
            "<name/><description/><devicePoolName/><locationName/><callingSearchSpaceName/>"
            "</returnedTags>"
            f"<skip>{max(0, int(skip))}</skip>"
            f"<first>{max(1, min(int(first), 500))}</first>"
        )
        root = self.execute_operation("listCtiRoutePoint", body)
        return self._extract_list_items(root, "ctiRoutePoint")

    def list_sip_trunks(self, name_pattern: str = "%", first: int = 200, skip: int = 0) -> List[Dict[str, Any]]:
        body = (
            f"<searchCriteria><name>{escape(name_pattern)}</name></searchCriteria>"
            "<returnedTags>"
            "<name/><description/><devicePoolName/><sipProfileName/><callingSearchSpaceName/>"
            "</returnedTags>"
            f"<skip>{max(0, int(skip))}</skip>"
            f"<first>{max(1, min(int(first), 500))}</first>"
        )
        root = self.execute_operation("listSipTrunk", body)
        return self._extract_list_items(root, "sipTrunk")

    def list_device_pools(self, name_pattern: str = "%", first: int = 200, skip: int = 0) -> List[Dict[str, Any]]:
        body = (
            f"<searchCriteria><name>{escape(name_pattern)}</name></searchCriteria>"
            "<returnedTags>"
            "<name/><callManagerGroupName/><dateTimeSettingName/><regionName/>"
            "</returnedTags>"
            f"<skip>{max(0, int(skip))}</skip>"
            f"<first>{max(1, min(int(first), 500))}</first>"
        )
        root = self.execute_operation("listDevicePool", body)
        return self._extract_list_items(root, "devicePool")

    @staticmethod
    def _element_to_dict(elem: ET.Element) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for child in elem:
            tag = child.tag.split("}")[-1]
            if len(child) == 0:
                result[tag] = child.text.strip() if child.text else None
            else:
                # Extract nested text or list of lines
                nested_text = child.text.strip() if child.text and child.text.strip() else None
                if tag == "lines":
                    lines = []
                    for line_el in child:
                        dn = None
                        part = None
                        for sub in line_el.iter():
                            st = sub.tag.split("}")[-1]
                            if st == "pattern" and sub.text:
                                dn = sub.text.strip()
                            elif st == "routePartitionName" and sub.text:
                                part = sub.text.strip()
                        if dn:
                            lines.append({"pattern": dn, "partition": part})
                    result["lines"] = lines
                else:
                    result[tag] = nested_text or AXLSoapExecutor._element_to_dict(child)
        return result

    @staticmethod
    def _extract_list_items(root: ET.Element, item_tag: str) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for elem in root.iter():
            if elem.tag.split("}")[-1] == item_tag:
                items.append(AXLSoapExecutor._element_to_dict(elem))
        return items
