"""Independent HTTPS SOAP AXL client for Cisco CUCM."""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

import httpx

from backend.connectivity.exceptions import (
    CUCMAXLAuthenticationError,
    CUCMAXLConnectionError,
    CUCMAXLSoapFaultError,
)
from backend.utils.config_loader import get_settings
from backend.utils.logger import get_logger
from backend.utils.validators import validate_readonly_sql

logger = get_logger("axl", category="axl")


@dataclass
class AXLConfig:
    host: str
    port: int = 8443
    username: str = ""
    password: str = ""
    verify_ssl: bool = False
    axl_version: str = "15.0"
    timeout: int = 30

    @classmethod
    def from_settings(cls, host: Optional[str] = None, port: Optional[int] = None) -> "AXLConfig":
        settings = get_settings()
        password = (
            settings.cucm_password.get_secret_value()
            if settings.cucm_password
            else ""
        )
        return cls(
            host=host or settings.cucm_host,
            port=port or settings.cucm_port,
            username=settings.cucm_username,
            password=password,
            verify_ssl=settings.effective_verify_ssl,
            axl_version=settings.cucm_axl_version,
        )


class CUCMAXLClient:
    """Lightweight HTTPS SOAP AXL client without external WSDL file dependency."""

    def __init__(self, config: Optional[AXLConfig] = None, transport: Optional[httpx.BaseTransport] = None):
        self.config = config or AXLConfig.from_settings()
        self._transport = transport

    @property
    def endpoint(self) -> str:
        return f"https://{self.config.host}:{self.config.port}/axl/"

    def _build_envelope(self, action: str, body_xml: str) -> str:
        ns = f"http://www.cisco.com/AXL/API/{self.config.axl_version}"
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
            f'xmlns:ns="{ns}">'
            "<soapenv:Header/>"
            "<soapenv:Body>"
            f"<ns:{action}>{body_xml}</ns:{action}>"
            "</soapenv:Body>"
            "</soapenv:Envelope>"
        )

    def _post_soap(self, action: str, body_xml: str) -> ET.Element:
        if not self.config.username or not self.config.password or self.config.password == "<SET_LOCALLY>":
            raise CUCMAXLAuthenticationError("CUCM AXL credentials are not configured.")

        envelope = self._build_envelope(action, body_xml)
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f"CUCM:DB ver={self.config.axl_version} {action}",
        }

        try:
            with httpx.Client(
                verify=self.config.verify_ssl,
                timeout=self.config.timeout,
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = client.post(
                    self.endpoint,
                    content=envelope.encode("utf-8"),
                    headers=headers,
                    auth=(self.config.username, self.config.password),
                )
        except httpx.TimeoutException as exc:
            logger.error("AXL request timeout to %s: %s", self.endpoint, exc)
            raise CUCMAXLConnectionError(f"AXL request timed out: {exc}") from exc
        except httpx.RequestError as exc:
            logger.error("AXL connection error to %s: %s", self.endpoint, exc)
            raise CUCMAXLConnectionError(f"AXL connection failed to {self.endpoint}: {exc}") from exc

        if response.status_code in (401, 403):
            logger.error("AXL authentication failed (HTTP %d)", response.status_code)
            raise CUCMAXLAuthenticationError(f"AXL authentication failed (HTTP {response.status_code}).")

        if response.status_code >= 500:
            fault_msg = self._extract_fault(response.text) or f"HTTP {response.status_code}"
            logger.error("AXL SOAP fault: %s", fault_msg)
            raise CUCMAXLSoapFaultError(f"AXL SOAP fault: {fault_msg}")

        if response.status_code != 200:
            raise CUCMAXLConnectionError(f"Unexpected AXL HTTP status {response.status_code}")

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            raise CUCMAXLSoapFaultError(f"Invalid XML returned by AXL: {exc}") from exc

        fault = self._extract_fault_from_tree(root)
        if fault:
            raise CUCMAXLSoapFaultError(f"AXL SOAP fault: {fault}")

        return root

    @staticmethod
    def _extract_fault_from_tree(root: ET.Element) -> Optional[str]:
        for elem in root.iter():
            if elem.tag.endswith("Fault"):
                faultstring = None
                for child in elem.iter():
                    if child.tag.endswith("faultstring") and child.text:
                        faultstring = child.text.strip()
                        break
                return faultstring or "Unknown SOAP Fault"
        return None

    def _extract_fault(self, xml_text: str) -> Optional[str]:
        try:
            root = ET.fromstring(xml_text)
            return self._extract_fault_from_tree(root)
        except ET.ParseError:
            return None

    def get_cucm_version(self) -> str:
        """Query CUCM version via AXL getCCMVersion."""
        logger.info("Querying AXL getCCMVersion on %s", self.config.host)
        root = self._post_soap("getCCMVersion", "")
        for elem in root.iter():
            if elem.tag.endswith("version") and elem.text:
                return elem.text.strip()
        return self.config.axl_version

    def execute_sql_query(self, sql: str) -> List[Dict[str, Any]]:
        """Execute a strict read-only SELECT query via AXL executeSQLQuery."""
        allowed, reason = validate_readonly_sql(sql)
        if not allowed:
            raise CUCMAXLSoapFaultError(reason)

        logger.info("Executing AXL SQL query on %s", self.config.host)
        root = self._post_soap("executeSQLQuery", f"<sql>{escape(sql)}</sql>")

        rows: List[Dict[str, Any]] = []
        for elem in root.iter():
            if elem.tag.endswith("row"):
                row_data: Dict[str, Any] = {}
                for col in elem:
                    tag = col.tag.split("}")[-1]
                    row_data[tag] = col.text.strip() if col.text else None
                rows.append(row_data)
        return rows

    def get_cluster_nodes(self) -> List[Dict[str, Any]]:
        """Retrieve CUCM cluster nodes from processnode table."""
        sql = (
            "SELECT pn.name, pn.description, np.name AS node_role "
            "FROM processnode pn "
            "LEFT JOIN typenodeusage np ON pn.tknodeusage = np.enum "
            "WHERE pn.systemnode = 'f'"
        )
        return self.execute_sql_query(sql)

    def test_connection(self) -> Dict[str, Any]:
        """Test AXL connectivity and return version/status."""
        version = self.get_cucm_version()
        return {"connected": True, "version": version, "endpoint": self.endpoint}
