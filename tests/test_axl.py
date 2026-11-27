"""Tests for independent HTTPS SOAP AXL client."""

import httpx
import pytest

from backend.connectivity.axl_client import AXLConfig, CUCMAXLClient
from backend.connectivity.exceptions import (
    CUCMAXLAuthenticationError,
    CUCMAXLSoapFaultError,
)


def test_axl_get_version_success():
    soap_resp = """<?xml version="1.0" encoding="utf-8"?>
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
      <soapenv:Body>
        <ns:getCCMVersionResponse xmlns:ns="http://www.cisco.com/AXL/API/15.0">
          <return><componentVersion><version>15.0.1.10000-14</version></componentVersion></return>
        </ns:getCCMVersionResponse>
      </soapenv:Body>
    </soapenv:Envelope>"""

    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=soap_resp))
    cfg = AXLConfig(host="10.197.206.141", port=8443, username="Administrator", password="secret")
    client = CUCMAXLClient(cfg, transport=transport)
    assert client.get_cucm_version() == "15.0.1.10000-14"


def test_axl_authentication_failure_401():
    transport = httpx.MockTransport(lambda req: httpx.Response(401, text="Unauthorized"))
    cfg = AXLConfig(host="10.197.206.141", port=8443, username="Administrator", password="wrong")
    client = CUCMAXLClient(cfg, transport=transport)
    with pytest.raises(CUCMAXLAuthenticationError):
        client.get_cucm_version()


def test_axl_soap_fault_handling():
    fault_xml = """<?xml version="1.0" encoding="utf-8"?>
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
      <soapenv:Body>
        <soapenv:Fault><faultstring>Invalid SQL syntax</faultstring></soapenv:Fault>
      </soapenv:Body>
    </soapenv:Envelope>"""
    transport = httpx.MockTransport(lambda req: httpx.Response(500, text=fault_xml))
    cfg = AXLConfig(host="10.197.206.141", port=8443, username="Administrator", password="secret")
    client = CUCMAXLClient(cfg, transport=transport)
    with pytest.raises(CUCMAXLSoapFaultError, match="Invalid SQL syntax"):
        client.execute_sql_query("SELECT name FROM processnode")


def test_axl_blocks_non_select_sql():
    cfg = AXLConfig(host="10.197.206.141", port=8443, username="Administrator", password="secret")
    client = CUCMAXLClient(cfg)
    with pytest.raises(CUCMAXLSoapFaultError, match="Only SELECT"):
        client.execute_sql_query("DELETE FROM processnode")
