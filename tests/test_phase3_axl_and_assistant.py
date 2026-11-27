"""Tests for Phase 3 CUCM AXL toolkit, RISPort70, AI Assistant, and chatbot routes."""

from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.agents.conversation_manager import ConversationManager
from backend.agents.cucm_agent import CUCMAssistantAgent
from backend.agents.intent_router import IntentRouter
from backend.agents.response_formatter import ResponseFormatter
from backend.connectivity.axl_client import AXLConfig
from backend.connectivity.exceptions import CUCMAXLSoapFaultError
from backend.integrations.cucm_axl.risport_client import RisPort70Client
from backend.integrations.cucm_axl.schema_manager import AXLSchemaManager
from backend.integrations.cucm_axl.sql_executor import ApprovedSQLExecutor, sanitize_sql_param
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry
from backend.integrations.cucm_axl.toolkit_loader import load_axl_toolkit
from backend.main import app
from backend.tools import register_all_cucm_tools


def test_axl_toolkit_loader_and_schema_manager():
    info = load_axl_toolkit("15.0")
    assert info["schema"]["axl_version"] == "15.0"
    assert info["tool_count"] >= 9
    assert "cucm_list_cti_route_points" in info["tools"]
    assert "cucm_get_phone_registration" in info["tools"]

    mgr = AXLSchemaManager(axl_version="15.0")
    assert mgr.is_operation_supported("getPhone") is True
    assert mgr.is_operation_supported("listCtiRoutePoint") is True


def test_sql_executor_blocks_unapproved_templates_and_sanitizes():
    assert sanitize_sql_param("SEP0011'; DROP TABLE device;--") == "SEP0011 DROP TABLE device"
    mock_axl = MagicMock()
    mock_axl.execute_sql_query.return_value = [{"name": "CTI_RP_1", "description": "Test"}]
    exec_sql = ApprovedSQLExecutor(axl_client=mock_axl)

    rows = exec_sql.execute_template("cti_route_points")
    assert len(rows) == 1
    with pytest.raises(CUCMAXLSoapFaultError):
        exec_sql.execute_template("arbitrary_drop_table")


def test_risport70_parses_registered_and_unregistered_phones():
    soap_xml = """<?xml version="1.0" encoding="utf-8"?>
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
      <soapenv:Body>
        <ns1:selectCmDeviceResponse xmlns:ns1="http://schemas.cisco.com/ast/soap">
          <ns1:selectCmDeviceReturn>
            <ns1:SelectCmDeviceResult>
              <ns1:TotalDevicesFound>2</ns1:TotalDevicesFound>
              <ns1:CmNodes>
                <ns1:item>
                  <ns1:Name>UCM15-HQ-SUB1</ns1:Name>
                  <ns1:CmDevices>
                    <ns1:item>
                      <ns1:Name>SEP001122334455</ns1:Name>
                      <ns1:Status>Registered</ns1:Status>
                      <ns1:IPAddress><ns1:item><ns1:IP>10.106.44.50</ns1:IP></ns1:item></ns1:IPAddress>
                      <ns1:DirNumber>1001</ns1:DirNumber>
                    </ns1:item>
                    <ns1:item>
                      <ns1:Name>SEP001122334466</ns1:Name>
                      <ns1:Status>UnRegistered</ns1:Status>
                    </ns1:item>
                  </ns1:CmDevices>
                </ns1:item>
              </ns1:CmNodes>
            </ns1:SelectCmDeviceResult>
          </ns1:selectCmDeviceReturn>
        </ns1:selectCmDeviceResponse>
      </soapenv:Body>
    </soapenv:Envelope>"""

    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=soap_xml))
    cfg = AXLConfig(host="10.197.206.141", username="Administrator", password="secret")
    ris = RisPort70Client(config=cfg, transport=transport)
    res = ris.select_cm_device()
    assert res["total_devices_found"] == 2
    assert len(res["devices"]) == 2
    assert res["devices"][0]["node_name"] == "UCM15-HQ-SUB1"
    assert res["devices"][0]["ip_address"] == "10.106.44.50"
    assert res["devices"][1]["status"] == "UnRegistered"


def test_assistant_agent_routing_and_unavailable_llm():
    reg = AXLToolRegistry()
    register_all_cucm_tools(reg)
    # Override handler for CTI route points with deterministic mock data
    reg.register(
        reg.get_spec("cucm_list_cti_route_points"),
        lambda params: {
            "count": 2,
            "rows": [
                {"name": "CTI_IVR_1", "description": "IVR", "device_pool": "Default", "location": "Hub_None", "directory_numbers": "5000"},
                {"name": "CTI_CCX_2", "description": "CCX", "device_pool": "Default", "location": "Hub_None", "directory_numbers": "5001"},
            ],
        },
    )

    agent = CUCMAssistantAgent(
        registry=reg,
        router=IntentRouter(),
        formatter=ResponseFormatter(),
        conversations=ConversationManager(),
    )
    reply = agent.ask("How many CTI Route Points are configured?", session_id="t1", execution_preference="tool_only")
    assert reply.tool_used == "cucm_list_cti_route_points"
    assert "2 configured CTI Route Point" in reply.content
    assert reply.ai_available is False
    assert reply.table is not None
    assert len(reply.table["rows"]) == 2


def test_assistant_api_routes():
    client = TestClient(app)
    tools_res = client.get("/api/assistant/tools")
    assert tools_res.status_code == 200
    assert len(tools_res.json()) >= 9

    del_res = client.delete("/api/assistant/history?session_id=test_api")
    assert del_res.status_code == 200
    assert client.get("/api/assistant/history?session_id=test_api").json() == []
