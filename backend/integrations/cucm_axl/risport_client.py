"""CUCM RisPort70 Real-Time Serviceability SOAP Client."""

import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

import httpx

from backend.connectivity.axl_client import AXLConfig
from backend.connectivity.exceptions import CUCMAXLAuthenticationError, CUCMAXLConnectionError
from backend.utils.logger import get_logger

logger = get_logger("risport70", category="axl")


class RisPort70Client:
    """Read-only SOAP client for CUCM RISService70 real-time device registration status."""

    def __init__(self, config: Optional[AXLConfig] = None, transport: Optional[httpx.BaseTransport] = None):
        self.config = config or AXLConfig.from_settings()
        self._transport = transport

    @property
    def endpoint(self) -> str:
        return f"https://{self.config.host}:{self.config.port}/realtimeservice2/services/RISService70"

    def select_cm_device(
        self,
        device_name: str = "*",
        device_class: str = "Phone",
        status: str = "Any",
        node_name: str = "",
        select_by: str = "Name",
        max_devices: int = 1000,
    ) -> Dict[str, Any]:
        """Query real-time device registration via RisPort70 selectCmDevice."""
        if not self.config.username or not self.config.password or self.config.password == "<SET_LOCALLY>":
            raise CUCMAXLAuthenticationError("CUCM RISPort70 credentials are not configured.")

        safe_item = escape((device_name or "*").strip())
        safe_class = escape(device_class)
        safe_status = escape(status)
        safe_node = escape((node_name or "").strip())
        safe_select_by = escape(select_by)
        limit = max(1, min(int(max_devices), 1000))

        soap_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:soap="http://schemas.cisco.com/ast/soap">'
            "<soapenv:Header/>"
            "<soapenv:Body>"
            "<soap:selectCmDevice>"
            "<soap:StateInfo></soap:StateInfo>"
            "<soap:CmSelectionCriteria>"
            f"<soap:MaxReturnedDevices>{limit}</soap:MaxReturnedDevices>"
            f"<soap:DeviceClass>{safe_class}</soap:DeviceClass>"
            "<soap:Model>255</soap:Model>"
            f"<soap:Status>{safe_status}</soap:Status>"
            f"<soap:NodeName>{safe_node}</soap:NodeName>"
            f"<soap:SelectBy>{safe_select_by}</soap:SelectBy>"
            "<soap:SelectItems>"
            f"<soap:item><soap:Item>{safe_item}</soap:Item></soap:item>"
            "</soap:SelectItems>"
            "<soap:Protocol>Any</soap:Protocol>"
            "<soap:DownloadStatus>Any</soap:DownloadStatus>"
            "</soap:CmSelectionCriteria>"
            "</soap:selectCmDevice>"
            "</soapenv:Body>"
            "</soapenv:Envelope>"
        )

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": '"http://schemas.cisco.com/ast/soap/selectCmDevice"',
        }

        try:
            with httpx.Client(
                verify=self.config.verify_ssl,
                timeout=self.config.timeout,
                trust_env=False,
                transport=self._transport,
            ) as client:
                resp = client.post(
                    self.endpoint,
                    content=soap_body.encode("utf-8"),
                    headers=headers,
                    auth=(self.config.username, self.config.password),
                )
        except httpx.TimeoutException as exc:
            raise CUCMAXLConnectionError(f"RISPort70 request timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise CUCMAXLConnectionError(f"RISPort70 connection failed: {exc}") from exc

        if resp.status_code in (401, 403):
            raise CUCMAXLAuthenticationError(f"RISPort70 authentication failed (HTTP {resp.status_code}).")
        if resp.status_code != 200:
            raise CUCMAXLConnectionError(f"RISPort70 returned HTTP {resp.status_code}")

        return self._parse_response(resp.text)

    @staticmethod
    def _parse_response(xml_text: str) -> Dict[str, Any]:
        root = ET.fromstring(xml_text)
        total_found = 0
        devices: List[Dict[str, Any]] = []

        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            if tag == "TotalDevicesFound" and elem.text and elem.text.strip().isdigit():
                total_found = int(elem.text.strip())

        for node_el in root.iter():
            if node_el.tag.split("}")[-1] != "CmNodes":
                continue
            for node_item in node_el:
                node_name = None
                for nc in node_item:
                    nt = nc.tag.split("}")[-1]
                    if nt == "Name" and nc.text:
                        node_name = nc.text.strip()
                    elif nt == "CmDevices":
                        for dev_item in nc:
                            dev = RisPort70Client._parse_device_elem(dev_item, node_name=node_name)
                            if dev and dev.get("name"):
                                devices.append(dev)

        # Fallback if response uses flat CmDevices without CmNodes wrapper
        if not devices:
            for dev_el in root.iter():
                if dev_el.tag.split("}")[-1] == "CmDevices":
                    for dev_item in dev_el:
                        dev = RisPort70Client._parse_device_elem(dev_item, node_name=None)
                        if dev and dev.get("name"):
                            devices.append(dev)

        return {
            "total_devices_found": total_found or len(devices),
            "devices": devices,
        }

    @staticmethod
    def _parse_device_elem(dev_item: ET.Element, node_name: Optional[str]) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "name": None,
            "status": "Unknown",
            "ip_address": None,
            "model": None,
            "description": None,
            "dir_number": None,
            "node_name": node_name,
            "active_load_id": None,
            "status_reason": None,
        }
        for child in dev_item:
            tag = child.tag.split("}")[-1]
            val = child.text.strip() if child.text else None
            if tag == "Name" and val:
                info["name"] = val
            elif tag == "Status" and val:
                info["status"] = val
            elif tag == "Model" and val:
                info["model"] = val
            elif tag == "Description" and val:
                info["description"] = val
            elif tag == "DirNumber" and val:
                info["dir_number"] = val
            elif tag in ("ActiveLoadID", "ActiveLoad") and val:
                info["active_load_id"] = val
            elif tag == "StatusReason" and val:
                info["status_reason"] = val
            elif tag in ("IpAddress", "IPAddress"):
                ip_text = None
                for sub in child.iter():
                    if sub.tag.split("}")[-1] == "IP" and sub.text and sub.text.strip():
                        ip_text = sub.text.strip()
                        break
                info["ip_address"] = ip_text or val
            elif tag in ("LinesStatus", "Lines"):
                dns = []
                for sub in child.iter():
                    if sub.tag.split("}")[-1] in ("DirNumber", "DirectoryNumber") and sub.text and sub.text.strip():
                        dns.append(sub.text.strip())
                if dns and not info["dir_number"]:
                    info["dir_number"] = ", ".join(dns)
        return info
