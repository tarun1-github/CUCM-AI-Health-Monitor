"""Tests for Phase 6B verified CUCM execution evidence, two-stage statuses, and zero-record vs failure handling."""

from pathlib import Path
from unittest.mock import MagicMock

import httpx

from backend.agents.conversation_manager import ConversationManager
from backend.agents.cucm_agent import CUCMAssistantAgent
from backend.agents.intent_router import IntentRouter
from backend.agents.response_formatter import ResponseFormatter
from backend.connectivity.axl_client import AXLConfig, CUCMAXLClient
from backend.integrations.cucm_axl.client import CUCMToolkitAXLClient
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry
from backend.tools.trunk_tools import register_trunk_tools


def _axl_toolkit_with_transport(transport: httpx.BaseTransport) -> CUCMToolkitAXLClient:
    cfg = AXLConfig(host="10.197.206.141", username="Administrator", password="pw")
    tk = CUCMToolkitAXLClient(cfg)
    tk.axl = CUCMAXLClient(cfg, transport=transport)
    tk.sql.client = tk.axl
    return tk


def test_axl_select_with_records_and_zero_records():
    xml_records = """<?xml version="1.0" encoding="utf-8"?>
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
      <soapenv:Body><ns:executeSQLQueryResponse xmlns:ns="http://www.cisco.com/AXL/API/15.0">
        <return><row><name>SIP_CUBE_1</name><description>CUBE</description></row></return>
      </ns:executeSQLQueryResponse></soapenv:Body>
    </soapenv:Envelope>"""
    tk1 = _axl_toolkit_with_transport(httpx.MockTransport(lambda r: httpx.Response(200, text=xml_records)))
    reg1 = AXLToolRegistry()
    register_trunk_tools(reg1, client=tk1)
    res1 = reg1.execute("cucm_list_sip_trunks", {})
    assert res1["success"] is True
    assert res1["count"] == 1
    assert res1["query_outcome"] == "SUCCESS_WITH_RECORDS"
    assert res1["authentication_status"] == "SUCCESS"

    xml_zero = """<?xml version="1.0" encoding="utf-8"?>
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
      <soapenv:Body><ns:executeSQLQueryResponse xmlns:ns="http://www.cisco.com/AXL/API/15.0"><return/></ns:executeSQLQueryResponse></soapenv:Body>
    </soapenv:Envelope>"""
    tk0 = _axl_toolkit_with_transport(httpx.MockTransport(lambda r: httpx.Response(200, text=xml_zero)))
    reg0 = AXLToolRegistry()
    register_trunk_tools(reg0, client=tk0)
    res0 = reg0.execute("cucm_list_sip_trunks", {})
    assert res0["success"] is True
    assert res0["count"] == 0
    assert res0["query_outcome"] == "SUCCESS_WITH_ZERO_RECORDS"


def test_axl_auth_failure_soap_fault_and_malformed_xml_never_return_zero_records():
    # 401 Auth failure
    tk_401 = _axl_toolkit_with_transport(httpx.MockTransport(lambda r: httpx.Response(401, text="Unauthorized")))
    reg_401 = AXLToolRegistry()
    register_trunk_tools(reg_401, client=tk_401)
    r_401 = reg_401.execute("cucm_list_sip_trunks", {})
    assert r_401["success"] is False
    assert r_401["retrieval_status"] == "AUTHENTICATION_FAILED"
    assert "count" not in r_401

    # 500 SOAP Fault
    fault_xml = "<soapenv:Envelope xmlns:soapenv='http://schemas.xmlsoap.org/soap/envelope/'><soapenv:Body><soapenv:Fault><faultstring>Table error</faultstring></soapenv:Fault></soapenv:Body></soapenv:Envelope>"
    tk_fault = _axl_toolkit_with_transport(httpx.MockTransport(lambda r: httpx.Response(500, text=fault_xml)))
    reg_fault = AXLToolRegistry()
    register_trunk_tools(reg_fault, client=tk_fault)
    r_fault = reg_fault.execute("cucm_list_sip_trunks", {})
    assert r_fault["success"] is False
    assert r_fault["retrieval_status"] == "QUERY_FAILED"

    # Malformed XML
    tk_bad = _axl_toolkit_with_transport(httpx.MockTransport(lambda r: httpx.Response(200, text="<not-valid-xml")))
    reg_bad = AXLToolRegistry()
    register_trunk_tools(reg_bad, client=tk_bad)
    r_bad = reg_bad.execute("cucm_list_sip_trunks", {})
    assert r_bad["success"] is False
    assert r_bad["retrieval_status"] == "QUERY_FAILED"


def test_agent_two_stage_evidence_tool_only_and_llm_failure_after_cucm_success():
    xml_zero = """<?xml version="1.0" encoding="utf-8"?>
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
      <soapenv:Body><ns:executeSQLQueryResponse xmlns:ns="http://www.cisco.com/AXL/API/15.0"><return/></ns:executeSQLQueryResponse></soapenv:Body>
    </soapenv:Envelope>"""
    tk0 = _axl_toolkit_with_transport(httpx.MockTransport(lambda r: httpx.Response(200, text=xml_zero)))
    reg = AXLToolRegistry()
    register_trunk_tools(reg, client=tk0)

    # 1. Tool-only without LLM
    agent_to = CUCMAssistantAgent(reg, IntentRouter(None), ResponseFormatter(None), ConversationManager())
    m_to = agent_to.ask("How many SIP trunks are configured on CUCM 10.197.206.141?", session_id="to1", execution_preference="tool_only")
    assert m_to.cucm_retrieval_status == "DATA_RETRIEVED"
    assert m_to.llm_generation_status == "NOT_STARTED"
    assert m_to.evidence is not None
    assert m_to.evidence.target["ip"] == "10.197.206.141"
    assert m_to.evidence.retrieval["template_id"] == "sip_trunks"
    assert m_to.evidence.retrieval["records_returned"] == 0

    # 2. LLM fails after CUCM retrieval succeeds -> FALLBACK, CUCM still DATA_RETRIEVED
    bad_llm = MagicMock()
    bad_llm.provider_name = "gemini"
    bad_llm.model = "gemini-3.1-flash-lite"
    bad_llm.generate.side_effect = [
        '{"intent_type": "CUCM_OPERATION", "tool_name": "cucm_list_sip_trunks", "params": {}}',
        RuntimeError("LLM rate limit"),
    ]
    agent_fb = CUCMAssistantAgent(reg, IntentRouter(bad_llm), ResponseFormatter(bad_llm), ConversationManager())
    m_fb = agent_fb.ask("How many SIP trunks are configured on CUCM 10.197.206.141?", session_id="fb1")
    assert m_fb.cucm_retrieval_status == "DATA_RETRIEVED"
    assert m_fb.llm_generation_status == "FALLBACK"
    assert m_fb.evidence.retrieval["query_outcome"] == "SUCCESS_WITH_ZERO_RECORDS"
    assert "password" not in (m_fb.evidence.raw_response or {})


def test_ui_displays_two_badges_and_evidence_details():
    js = Path("frontend/assets/js/dashboard.js").read_text(encoding="utf-8")
    assert "cucmStatusBadge" in js
    assert "aiStatusBadge" in js
    assert "View CUCM Execution Evidence" in js
    assert "View Raw CUCM Response" in js
