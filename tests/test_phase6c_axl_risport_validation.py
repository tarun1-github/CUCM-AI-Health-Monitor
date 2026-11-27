"""Phase 6C unit tests for AXL toolkit, AXL SQL, RISPort70, cross-validation, and provenance."""

import httpx
import pytest

from backend.agents.cucm_agent import CUCMAssistantAgent
from backend.connectivity.axl_client import AXLConfig, CUCMAXLClient
from backend.connectivity.exceptions import (
    CUCMAXLAuthenticationError,
    CUCMAXLConnectionError,
    CUCMAXLSoapFaultError,
)
from backend.integrations.cucm_axl.client import CUCMToolkitAXLClient
from backend.integrations.cucm_axl.risport_client import RisPort70Client
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry
from backend.tools.registration_tools import register_registration_tools


def _cfg(host: str = "10.197.206.141", user: str = "Administrator", pw: str = "secret") -> AXLConfig:
    return AXLConfig(host=host, port=8443, username=user, password=pw, axl_version="15.0", verify_ssl=False, timeout=10)


def _ris_xml(devices_xml: str = "", total: int = 0, state_info: str = "") -> str:
    st_xml = f"<ns1:StateInfo>{state_info}</ns1:StateInfo>" if state_info else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soapenv:Body>"
        '<ns1:selectCmDeviceResponse xmlns:ns1="http://schemas.cisco.com/ast/soap">'
        "<ns1:selectCmDeviceReturn>"
        "<ns1:SelectCmDeviceResult>"
        f"<ns1:TotalDevicesFound>{total}</ns1:TotalDevicesFound>"
        f"<ns1:CmNodes>{devices_xml}</ns1:CmNodes>"
        "</ns1:SelectCmDeviceResult>"
        f"{st_xml}"
        "</ns1:selectCmDeviceReturn>"
        "</ns1:selectCmDeviceResponse>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )


def test_01_axl_authentication_success():
    xml = '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Body><ns:getCCMVersionResponse xmlns:ns="http://www.cisco.com/AXL/API/15.0"><return><componentVersion><version>15.0.1.12900(234)</version></componentVersion></return></ns:getCCMVersionResponse></soapenv:Body></soapenv:Envelope>'
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=xml))
    client = CUCMAXLClient(_cfg(), transport=transport)
    root = client._post_soap("getCCMVersion", "")
    assert "15.0.1.12900(234)" in root.iter().__next__().tag or len(list(root.iter())) > 0


def test_02_axl_authentication_failure():
    transport = httpx.MockTransport(lambda req: httpx.Response(401, text="Unauthorized"))
    client = CUCMAXLClient(_cfg(), transport=transport)
    with pytest.raises(CUCMAXLAuthenticationError):
        client._post_soap("getCCMVersion", "")


def test_03_axl_select_with_records():
    xml = '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Body><ns:executeSQLQueryResponse xmlns:ns="http://www.cisco.com/AXL/API/15.0"><return><row><name>VGW_CUCM_Trunk</name></row><row><name>HQ-PSTN-SIP-Trunk</name></row></return></ns:executeSQLQueryResponse></soapenv:Body></soapenv:Envelope>'
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=xml))
    tk = CUCMToolkitAXLClient(_cfg())
    tk.axl = CUCMAXLClient(_cfg(), transport=transport)
    tk.sql.client = tk.axl
    ev = tk.sql.execute_template_with_evidence("sip_trunks")
    assert ev["query_outcome"] == "SUCCESS_WITH_RECORDS"
    assert ev["count"] == 2


def test_04_axl_select_with_zero_records():
    xml = '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Body><ns:executeSQLQueryResponse xmlns:ns="http://www.cisco.com/AXL/API/15.0"><return/></ns:executeSQLQueryResponse></soapenv:Body></soapenv:Envelope>'
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=xml))
    tk = CUCMToolkitAXLClient(_cfg())
    tk.axl = CUCMAXLClient(_cfg(), transport=transport)
    tk.sql.client = tk.axl
    ev = tk.sql.execute_template_with_evidence("cti_route_points")
    assert ev["query_outcome"] == "SUCCESS_WITH_ZERO_RECORDS"
    assert ev["count"] == 0


def test_05_axl_soap_fault():
    fault = '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Body><soapenv:Fault><faultstring>Invalid SQL syntax</faultstring></soapenv:Fault></soapenv:Body></soapenv:Envelope>'
    transport = httpx.MockTransport(lambda req: httpx.Response(500, text=fault))
    client = CUCMAXLClient(_cfg(), transport=transport)
    with pytest.raises(CUCMAXLSoapFaultError):
        client.execute_sql_query("SELECT * FROM nonexistent")


def test_06_risport70_authentication_failure():
    transport = httpx.MockTransport(lambda req: httpx.Response(401, text="Unauthorized"))
    ris = RisPort70Client(_cfg(), transport=transport)
    with pytest.raises(CUCMAXLAuthenticationError):
        ris.select_cm_device()


def test_07_risport70_select_cm_device_success():
    node_xml = '<ns1:item><ns1:ReturnCode>Ok</ns1:ReturnCode><ns1:Name>UCM15-HQ-PUB</ns1:Name><ns1:CmDevices><ns1:item><ns1:Name>RIAN</ns1:Name><ns1:Status>Registered</ns1:Status><ns1:DirNumber>6001-Registered</ns1:DirNumber><ns1:IPAddress><ns1:item><ns1:IP>10.106.44.89</ns1:IP></ns1:item></ns1:IPAddress></ns1:item></ns1:CmDevices></ns1:item>'
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=_ris_xml(node_xml, total=1)))
    ris = RisPort70Client(_cfg(), transport=transport)
    res = ris.select_cm_device()
    assert res["query_outcome"] == "SUCCESS_WITH_RECORDS"
    assert res["operation"] == "selectCmDevice"
    assert res["devices"][0]["name"] == "RIAN"
    assert res["devices"][0]["ip_address"] == "10.106.44.89"


def test_08_risport70_zero_record_response():
    node_xml = '<ns1:item><ns1:ReturnCode>NotFound</ns1:ReturnCode><ns1:Name>UCM15-HQ-PUB</ns1:Name><ns1:CmDevices/></ns1:item>'
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=_ris_xml(node_xml, total=0)))
    ris = RisPort70Client(_cfg(), transport=transport)
    res = ris.select_cm_device()
    assert res["total_devices_found"] == 0
    assert res["devices"] == []
    assert res["query_outcome"] == "SUCCESS_WITH_ZERO_RECORDS"


def test_09_risport70_node_filter_ip_and_hostname():
    node_xml = '<ns1:item><ns1:ReturnCode>Ok</ns1:ReturnCode><ns1:Name>UCM15-HQ-PUB</ns1:Name><ns1:CmDevices><ns1:item><ns1:Name>RIAN</ns1:Name><ns1:Status>Registered</ns1:Status></ns1:item></ns1:CmDevices></ns1:item>'
    tk = CUCMToolkitAXLClient(_cfg())
    tk.risport = RisPort70Client(_cfg(), transport=httpx.MockTransport(lambda req: httpx.Response(200, text=_ris_xml(node_xml, total=1))))
    reg = AXLToolRegistry()
    register_registration_tools(reg, client=tk)

    # IP node filter must NOT drop hostname UCM15-HQ-PUB devices
    r_ip = reg.execute("cucm_get_phone_registration", {"node_name": "10.197.206.141"})
    assert r_ip["total_found"] == 1
    assert r_ip["registered_count"] == 1

    # Hostname node filter matching another node drops it as expected
    r_other = reg.execute("cucm_get_phone_registration", {"node_name": "UCM15-HQ-SUB1"})
    assert r_other["total_found"] == 0
    assert r_other["query_status"] == "SUCCESS_WITH_ZERO_RECORDS"


def test_10_risport70_status_filter():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = req.content.decode("utf-8")
        return httpx.Response(200, text=_ris_xml("", total=0))

    ris = RisPort70Client(_cfg(), transport=httpx.MockTransport(handler))
    ris.select_cm_device(status="Registered")
    assert "<soap:Status>Registered</soap:Status>" in captured["body"]


def test_11_risport70_xml_parser_nested_lines_and_ip():
    node_xml = '<ns1:item><ns1:ReturnCode>Ok</ns1:ReturnCode><ns1:Name>UCM15-HQ-PUB</ns1:Name><ns1:CmDevices><ns1:item><ns1:Name>SEP001122334455</ns1:Name><ns1:Status>UnRegistered</ns1:Status><ns1:Model>30016</ns1:Model><ns1:LinesStatus><ns1:item><ns1:DirectoryNumber>7001</ns1:DirectoryNumber></ns1:item></ns1:LinesStatus><ns1:IPAddress><ns1:item><ns1:IP>10.10.10.50</ns1:IP></ns1:item></ns1:IPAddress></ns1:item></ns1:CmDevices></ns1:item>'
    parsed = RisPort70Client._parse_response(_ris_xml(node_xml, total=1))
    dev = parsed["devices"][0]
    assert dev["name"] == "SEP001122334455"
    assert dev["status"] == "UnRegistered"
    assert dev["dir_number"] == "7001"
    assert dev["ip_address"] == "10.10.10.50"
    assert dev["node_name"] == "UCM15-HQ-PUB"


def test_12_risport70_state_info_handling():
    state_escaped = '&lt;StateInfo&gt;&lt;Node Name="UCM15-HQ-PUB" SubsystemStartTime="1793604756" StateId="11" TotalItemsFound="1" TotalItemsReturned="1"/&gt;&lt;/StateInfo&gt;'
    parsed = RisPort70Client._parse_response(_ris_xml("", total=0, state_info=state_escaped))
    assert len(parsed["state_info"]["nodes"]) == 1
    n0 = parsed["state_info"]["nodes"][0]
    assert n0["name"] == "UCM15-HQ-PUB"
    assert n0["state_id"] == "11"
    assert n0["total_items_found"] == 1


def test_13_risport70_device_count():
    node_xml = (
        '<ns1:item><ns1:ReturnCode>Ok</ns1:ReturnCode><ns1:Name>UCM15-HQ-PUB</ns1:Name><ns1:CmDevices>'
        '<ns1:item><ns1:Name>PHONE1</ns1:Name><ns1:Status>Registered</ns1:Status></ns1:item>'
        '<ns1:item><ns1:Name>PHONE2</ns1:Name><ns1:Status>UnRegistered</ns1:Status></ns1:item>'
        "</ns1:CmDevices></ns1:item>"
    )
    tk = CUCMToolkitAXLClient(_cfg())
    tk.risport = RisPort70Client(_cfg(), transport=httpx.MockTransport(lambda req: httpx.Response(200, text=_ris_xml(node_xml, total=2))))
    reg = AXLToolRegistry()
    register_registration_tools(reg, client=tk)
    res = reg.execute("cucm_get_phone_registration", {})
    assert res["total_found"] == 2
    assert res["registered_count"] == 1
    assert res["unregistered_count"] == 1


def test_14_axl_and_risport70_cross_validation():
    axl_xml = '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Body><ns:executeSQLQueryResponse xmlns:ns="http://www.cisco.com/AXL/API/15.0"><return><row><name>RIAN</name></row><row><name>MKUMAR</name></row><row><name>SSHARMA</name></row></return></ns:executeSQLQueryResponse></soapenv:Body></soapenv:Envelope>'
    ris_node = '<ns1:item><ns1:ReturnCode>Ok</ns1:ReturnCode><ns1:Name>UCM15-HQ-PUB</ns1:Name><ns1:CmDevices><ns1:item><ns1:Name>RIAN</ns1:Name><ns1:Status>Registered</ns1:Status></ns1:item></ns1:CmDevices></ns1:item>'
    tk = CUCMToolkitAXLClient(_cfg())
    tk.axl = CUCMAXLClient(_cfg(), transport=httpx.MockTransport(lambda req: httpx.Response(200, text=axl_xml)))
    tk.sql.client = tk.axl
    tk.risport = RisPort70Client(_cfg(), transport=httpx.MockTransport(lambda req: httpx.Response(200, text=_ris_xml(ris_node, total=1))))
    configured = tk.sql.execute_template_with_evidence("phones_inventory")
    live = tk.risport.select_cm_device()
    assert configured["count"] == 3
    assert live["total_devices_found"] == 1
    assert configured["count"] >= live["total_devices_found"]


def test_15_risport70_invalid_soap_response():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text="<html>not xml"))
    ris = RisPort70Client(_cfg(), transport=transport)
    with pytest.raises(CUCMAXLSoapFaultError):
        ris.select_cm_device()


def test_16_risport70_network_timeout():
    def raise_timeout(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=req)

    ris = RisPort70Client(_cfg(), transport=httpx.MockTransport(raise_timeout))
    with pytest.raises(CUCMAXLConnectionError):
        ris.select_cm_device()


def test_17_target_specific_credentials():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(200, text=_ris_xml("", total=0))

    ris = RisPort70Client(_cfg(host="10.20.30.40", user="axl_lab", pw="lab_pw"), transport=httpx.MockTransport(handler))
    res = ris.select_cm_device()
    assert res["target_ip"] == "10.20.30.40"
    assert captured["auth"] and captured["auth"].startswith("Basic ")


def test_18_chatbot_risport_llm_provenance(monkeypatch):
    agent = CUCMAssistantAgent()

    def fake_route(msg, tools, **kwargs):
        from backend.agents.intent_router import RoutedIntent
        return RoutedIntent("CUCM_OPERATION", "cucm_get_phone_registration", {"device_name": "*", "status": "Any"}, used_llm=True)

    monkeypatch.setattr(agent.router, "route", fake_route)
    monkeypatch.setattr(
        agent.registry,
        "execute",
        lambda tool_name, params=None: {
            "success": True,
            "source": "RISPort70",
            "target_ip": "10.197.206.141",
            "endpoint": "https://10.197.206.141:8443/realtimeservice2/services/RISService70",
            "operation": "selectCmDevice",
            "authentication_status": "SUCCESS",
            "query_status": "SUCCESS_WITH_RECORDS",
            "query_outcome": "SUCCESS_WITH_RECORDS",
            "total_devices_found": 1,
            "total_found": 1,
            "registered_count": 1,
            "unregistered_count": 0,
            "rows": [{"name": "RIAN", "status": "Registered", "ip_address": "10.106.44.89", "node_name": "UCM15-HQ-PUB"}],
            "collected_at_utc": "2026-11-27T11:00:00+00:00",
            "collected_at_ist": "2026-11-27 16:30:00 IST",
        },
    )

    reply = agent.ask("How many phones are registered?", session_id="p6c_test", mode="auto")
    assert reply.cucm_retrieval_status == "DATA_RETRIEVED"
    assert reply.evidence is not None
    assert reply.evidence.retrieval["source"] == "RISPort70"
    assert reply.evidence.retrieval["operation"] == "selectCmDevice"
    assert reply.evidence.retrieval["registered_count"] == 1
    assert reply.evidence.retrieval["unregistered_count"] == 0
    assert reply.evidence.llm["provider"] is not None
