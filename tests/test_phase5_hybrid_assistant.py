"""Tests for Phase 5 Hybrid General-Purpose + CUCM AI Assistant."""

from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from backend.agents.conversation_manager import ConversationManager
from backend.agents.cucm_agent import CUCMAssistantAgent
from backend.agents.intent_router import IntentRouter
from backend.agents.response_formatter import ResponseFormatter
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry
from backend.main import app
from backend.tools import register_all_cucm_tools


def _mock_registry() -> AXLToolRegistry:
    reg = AXLToolRegistry()
    register_all_cucm_tools(reg)
    reg.register(
        reg.get_spec("cucm_list_cti_route_points"),
        lambda p: {"count": 3, "rows": [{"name": "CTI_RP_1"}]},
    )
    reg.register(
        reg.get_spec("cucm_get_phone_registration"),
        lambda p: {"total_found": 2, "registered_count": 1, "unregistered_count": 1, "rows": [{"name": "SEP1", "status": "Registered"}]},
    )
    reg.register(
        reg.get_spec("cucm_list_sip_trunks"),
        lambda p: {"count": 1, "rows": [{"name": "SIP_TRUNK_CUBE", "device_pool": "DP_HQ"}]},
    )
    return reg


def test_what_is_css_routes_to_general_ai_without_line_lookup():
    reg = _mock_registry()
    llm = MagicMock()
    llm.provider_name = "gemini"
    llm.model = "gemini-3.1-flash-lite"
    llm.generate.side_effect = [
        '{"intent_type": "GENERAL_AI", "tool_name": null, "params": {}, "reason": "Web CSS question"}',
        "CSS stands for Cascading Style Sheets, used to style web pages.",
    ]
    agent = CUCMAssistantAgent(reg, IntentRouter(llm), ResponseFormatter(llm), ConversationManager())
    reply = agent.ask("What is CSS?", session_id="css1")
    assert reply.execution_mode == "GENERAL_AI"
    assert reply.tool_used is None
    assert "Cascading Style Sheets" in reply.content


def test_what_is_calling_search_space_does_not_call_line_lookup():
    reg = _mock_registry()
    llm = MagicMock()
    llm.provider_name = "gemini"
    llm.model = "gemini-3.1-flash-lite"
    llm.generate.side_effect = [
        '{"intent_type": "GENERAL_AI", "tool_name": null, "params": {}, "reason": "Conceptual CUCM theory"}',
        "A Calling Search Space (CSS) in CUCM is an ordered list of route partitions.",
    ]
    agent = CUCMAssistantAgent(reg, IntentRouter(llm), ResponseFormatter(llm), ConversationManager())
    reply = agent.ask("What is Calling Search Space in CUCM?", session_id="css2")
    assert reply.execution_mode == "GENERAL_AI"
    assert reply.tool_used is None


def test_cti_and_phone_reg_and_sip_trunks_use_cucm_tools():
    reg = _mock_registry()
    llm = MagicMock()
    llm.provider_name = "gemini"
    llm.model = "gemini-3.1-flash-lite"
    llm.generate.side_effect = [
        '{"intent_type": "CUCM_OPERATION", "tool_name": "cucm_list_cti_route_points", "params": {}}',
        "There are 3 configured CTI Route Points.",
        '{"intent_type": "CUCM_OPERATION", "tool_name": "cucm_get_phone_registration", "params": {"status": "Any"}}',
        "1 phone is registered.",
        '{"intent_type": "CUCM_OPERATION", "tool_name": "cucm_list_sip_trunks", "params": {}}',
        "Found 1 SIP trunk: SIP_TRUNK_CUBE.",
    ]
    agent = CUCMAssistantAgent(reg, IntentRouter(llm), ResponseFormatter(llm), ConversationManager())

    r_cti = agent.ask("How many CTI Route Points are configured?", session_id="c1")
    assert r_cti.tool.name == "cucm_list_cti_route_points" and r_cti.tool.source == "AXL SQL"

    r_reg = agent.ask("How many phones are registered?", session_id="c1")
    assert r_reg.tool.name == "cucm_get_phone_registration" and r_reg.tool.source == "RISPort70"

    r_sip = agent.ask("Show all SIP trunks.", session_id="c1")
    assert r_sip.tool.name == "cucm_list_sip_trunks" and r_sip.tool.source == "AXL SQL"


def test_python_and_sip_architecture_use_general_ai():
    reg = _mock_registry()
    llm = MagicMock()
    llm.provider_name = "gemini"
    llm.model = "gemini-3.1-flash-lite"
    llm.generate.side_effect = [
        '{"intent_type": "GENERAL_AI", "tool_name": null, "params": {}}',
        "import paramiko\n# Paramiko example",
        '{"intent_type": "GENERAL_AI", "tool_name": null, "params": {}}',
        "SIP trunks use INVITE/200 OK/ACK signaling.",
    ]
    agent = CUCMAssistantAgent(reg, IntentRouter(llm), ResponseFormatter(llm), ConversationManager())
    r1 = agent.ask("Write a Python script to connect to CUCM using Paramiko.", session_id="g1")
    r2 = agent.ask("Explain Cisco SIP trunk architecture.", session_id="g1")
    assert r1.execution_mode == "GENERAL_AI" and r1.tool_used is None
    assert r2.execution_mode == "GENERAL_AI" and r2.tool_used is None


def test_hybrid_query_combines_cucm_data_and_llm_explanation():
    reg = _mock_registry()
    llm = MagicMock()
    llm.provider_name = "gemini"
    llm.model = "gemini-3.1-flash-lite"
    llm.generate.side_effect = [
        '{"intent_type": "HYBRID", "tool_name": "cucm_list_sip_trunks", "params": {}}',
        "Retrieved CUCM Facts: SIP_TRUNK_CUBE in DP_HQ.\nTechnical Explanation: SIP trunks route calls to CUBE.",
    ]
    agent = CUCMAssistantAgent(reg, IntentRouter(llm), ResponseFormatter(llm), ConversationManager())
    reply = agent.ask("Show my configured SIP trunks and explain their purpose.", session_id="h1")
    assert reply.execution_mode == "HYBRID"
    assert reply.tool.name == "cucm_list_sip_trunks"
    assert reply.tool.success is True
    assert reply.llm.invocation_success is True


def test_ambiguous_abbreviation_requests_clarification():
    reg = _mock_registry()
    agent = CUCMAssistantAgent(reg, IntentRouter(llm_client=None), ResponseFormatter(llm_client=None), ConversationManager())
    reply = agent.ask("Check CSS", session_id="cl1", execution_preference="tool_only")
    assert reply.execution_mode == "CLARIFICATION_REQUIRED"
    assert reply.tool_used is None


def test_followup_retains_conversation_context_and_manual_modes():
    reg = _mock_registry()
    llm = MagicMock()
    llm.provider_name = "gemini"
    llm.model = "gemini-3.1-flash-lite"
    llm.generate.side_effect = [
        "Python is a high-level language.",
        "Here are 5 practical examples of Python...",
        '{"intent_type": "CUCM_OPERATION", "tool_name": "cucm_get_phone_registration", "params": {}}',
        "1 phone is registered.",
    ]
    convs = ConversationManager()
    agent = CUCMAssistantAgent(reg, IntentRouter(llm), ResponseFormatter(llm), convs)

    # Manual GENERAL AI mode never executes CUCM tools
    r1 = agent.ask("What is Python?", session_id="mem1", mode="general")
    r2 = agent.ask("Give me five practical examples.", session_id="mem1", mode="general")
    assert r1.execution_mode == "GENERAL_AI" and r1.tool_used is None
    assert r2.execution_mode == "GENERAL_AI" and r2.tool_used is None
    assert len(convs.get_history("mem1")) == 4

    # Manual CUCM mode uses approved tools
    r3 = agent.ask("How many phones are registered?", session_id="mem2", mode="cucm")
    assert r3.execution_mode == "LLM_GENERATED"
    assert r3.tool.name == "cucm_get_phone_registration"


def test_phase5_api_request_format_and_ui_mode_selector():
    client = TestClient(app)
    res = client.post(
        "/api/assistant/chat",
        json={"message": "Check CSS", "mode": "auto", "conversation_id": "p5_api", "execution_preference": "tool_only"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["selected_mode"] == "auto"
    assert data["execution_mode"] == "CLARIFICATION_REQUIRED"

    html = Path("frontend/index.html").read_text(encoding="utf-8")
    js = Path("frontend/assets/js/dashboard.js").read_text(encoding="utf-8")
    assert "modeBtnAuto" in html and "modeBtnGeneral" in html and "modeBtnCucm" in html
    assert "setAssistantMode" in js
