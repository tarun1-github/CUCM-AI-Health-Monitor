"""CUCM PerfmonService2 SOAP API client for live Processor and Memory counters."""

import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

import httpx

from backend.connectivity.axl_client import AXLConfig
from backend.connectivity.exceptions import CUCMAXLAuthenticationError, CUCMAXLConnectionError


class CUCMPerfMonClient:
    """Read-only SOAP client for Cisco CUCM PerfmonService2."""

    def __init__(self, config: Optional[AXLConfig] = None, transport: Optional[httpx.BaseTransport] = None):
        self.config = config or AXLConfig.from_settings()
        self._transport = transport

    @property
    def endpoint(self) -> str:
        return f"https://{self.config.host}:{self.config.port}/perfmonservice2/services/PerfmonService"

    def collect_counter_data(self, perf_object: str = "Processor", host_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Invoke perfmonCollectCounterData for a PerfMon object (e.g., Processor, Memory)."""
        if not self.config.username or not self.config.password or self.config.password == "<SET_LOCALLY>":
            raise CUCMAXLAuthenticationError("CUCM PerfMon credentials are not configured.")

        target = escape((host_name or self.config.host).strip())
        obj = escape(perf_object.strip())
        soap_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:soap="http://schemas.cisco.com/ast/soap">'
            "<soapenv:Header/>"
            "<soapenv:Body>"
            "<soap:perfmonCollectCounterData>"
            f"<soap:Host>{target}</soap:Host>"
            f"<soap:Object>{obj}</soap:Object>"
            "</soap:perfmonCollectCounterData>"
            "</soapenv:Body>"
            "</soapenv:Envelope>"
        )
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": '"perfmonCollectCounterData"',
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
            raise CUCMAXLConnectionError(f"PerfMon request timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise CUCMAXLConnectionError(f"PerfMon connection failed: {exc}") from exc

        if resp.status_code in (401, 403):
            raise CUCMAXLAuthenticationError(f"PerfMon authentication failed (HTTP {resp.status_code}).")
        if resp.status_code != 200:
            raise CUCMAXLConnectionError(f"PerfMon returned HTTP {resp.status_code}: {resp.text[:200]}")

        return self._parse_counters(resp.text)

    @staticmethod
    def _parse_counters(xml_text: str) -> List[Dict[str, Any]]:
        root = ET.fromstring(xml_text)
        counters: List[Dict[str, Any]] = []
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            if tag in ("CounterInfo", "perfmonCollectCounterDataReturn", "item"):
                name = None
                value = None
                for child in elem:
                    ct = child.tag.split("}")[-1]
                    if ct == "Name" and child.text:
                        name = child.text.strip()
                    elif ct == "Value" and child.text is not None:
                        try:
                            value = float(child.text.strip())
                        except ValueError:
                            value = child.text.strip()
                if name and value is not None:
                    counters.append({"name": name, "value": value})
        return counters

    def collect_processor_counters(self, host_name: Optional[str] = None) -> Dict[str, Any]:
        """Collect and normalize Processor counters from CUCM PerfMon API."""
        raw_counters = self.collect_counter_data("Processor", host_name=host_name)
        if not raw_counters:
            raise CUCMAXLConnectionError("PerfMon returned no Processor counters.")

        # Prefer _Total instance counters
        total_map: Dict[str, float] = {}
        fallback_map: Dict[str, float] = {}
        for item in raw_counters:
            cname = item["name"]
            val = item["value"]
            if not isinstance(val, (int, float)):
                continue
            metric = cname.rsplit("\\", 1)[-1].strip().lower()
            if "(_total)" in cname.lower():
                total_map[metric] = float(val)
            else:
                fallback_map.setdefault(metric, float(val))

        src = total_map or fallback_map
        cpu_idle = src["idle percentage"] if "idle percentage" in src else src.get("% idle time")
        cpu_pct = src["% cpu time"] if "% cpu time" in src else src.get("% processor time")
        if cpu_pct is None and cpu_idle is not None:
            cpu_pct = round(max(0.0, min(100.0, 100.0 - float(cpu_idle))), 2)
        if cpu_idle is None and cpu_pct is not None:
            cpu_idle = round(max(0.0, min(100.0, 100.0 - float(cpu_pct))), 2)

        return {
            "cpu_utilization_pct": round(float(cpu_pct), 2) if cpu_pct is not None else None,
            "cpu_idle_pct": round(float(cpu_idle), 2) if cpu_idle is not None else None,
            "system_pct": src.get("system percentage"),
            "user_pct": src.get("user percentage"),
            "iowait_pct": src.get("iowait percentage"),
            "softirq_pct": src.get("softirq percentage"),
            "irq_pct": src.get("irq percentage"),
            "raw_counter_count": len(raw_counters),
        }
