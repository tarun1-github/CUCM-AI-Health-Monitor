"""Tests for Phase 4 real LLM integration, error categorization, provenance modes, and floating UI."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from backend.agents.conversation_manager import ConversationManager
from backend.agents.cucm_agent import CUCMAssistantAgent
from backend.agents.intent_router import IntentRouter
from backend.agents.response_formatter import ResponseFormatter
from backend.ai.gemini_client import GeminiLLMClient
from backend.ai.ollama_client import OllamaLLMClient
from backend.ai.openai_client import OpenAILLMClient
from backend.ai.provider_manager import LLMProviderManager, categorize_llm_error, sanitize_llm_error
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry
from backend.main import app
from backend.tools import register_all_cucm_tools


def test_provider_initializations_and_error_categorization():
    ol = OllamaLLMClient(base_url="http://localhost:11434/", model="llama3.1")
    assert ol.provider_name == "ollama"
    assert ol.base_url == "http://localhost:11434"

    gm = GeminiLLMClient(api_key="AIzaSyDummyKey123456", model="gemini-2.0-flash")
    assert gm.provider_name == "gemini"

    oa = OpenAILLMClient(api_key="sk-DummyKey123456", model="gpt-4o-mini")
    assert oa.provider_name == "openai"

    assert categorize_llm_error("WinError 10061 Connection refused") == "Connection refused / Unreachable"
    assert categorize_llm_error("HTTP 401 Unauthorized") == "Authentication failure / Invalid API key"
    assert categorize_llm_error("HTTP 404 model not found") == "Model not found"
    assert categorize_llm_error("ReadTimeout: timed out") == "Timeout"
    assert categorize_llm_error("HTTP 429 rate limit") == "Rate limit"
    assert "[REDACTED_GEMINI_KEY]" in sanitize_llm_error("Error with AIzaSyAbCdEfGhIjKlMnOp")


def test_provider_manager_missing_credentials_and_connection_tests():
    mgr = LLMProviderManager()
    mgr.configure("gemini", "gemini-2.0-flash")
    with patch("backend.ai.provider_manager.get_settings") as mock_s:
        s = MagicMock()
        s.gemini_api_key = None
        s.resolved_gemini_api_key = ""
        s.gemini_model = "gemini-2.0-flash"
        mock_s.return_value = s
        conn = mgr.test_connection()
        assert conn["connected"] is False
        assert mgr.provider_available is False
        inf = mgr.test_inference()
        assert inf["success"] is False
        assert inf["error_category"] == "Not configured"


def test_provider_manager_ollama_model_not_found_and_success():
    mgr = LLMProviderManager()
    mgr.configure("ollama", "missing-model")

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"models": [{"name": "llama3:latest"}]}
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.return_value = mock_resp
        res = mgr.test_connection()
        assert res["connected"] is True
        assert res["model_available"] is False
        assert mgr.provider_available is False

    mgr.configure("ollama", "llama3")
    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.return_value = mock_resp
        res2 = mgr.test_connection()
        assert res2["connected"] is True
        assert res2["model_available"] is True
        assert mgr.provider_available is True


def test_execution_modes_llm_generated_fallback_tool_only_and_failed():
    reg = AXLToolRegistry()
    register_all_cucm_tools(reg)
    reg.register(
        reg.get_spec("cucm_get_phone_registration"),
        lambda params: {"total_found": 3, "registered_count": 2, "unregistered_count": 1, "rows": [{"name": "SEP1", "status": "Registered"}]},
    )

    # 1. LLM_GENERATED
    fake_llm = MagicMock()
    fake_llm.provider_name = "ollama"
    fake_llm.model = "llama3"
    fake_llm.generate.side_effect = [
        '{"tool_name": "cucm_get_phone_registration", "params": {"status": "Any"}, "reason": "phone reg"}',
        "There are 2 registered phones and 1 unregistered phone.",
    ]
    agent_llm = CUCMAssistantAgent(
        registry=reg,
        router=IntentRouter(llm_client=fake_llm),
        formatter=ResponseFormatter(llm_client=fake_llm),
        conversations=ConversationManager(),
    )
    msg_llm = agent_llm.ask("How many phones are registered?", session_id="s_llm")
    assert msg_llm.execution_mode == "LLM_GENERATED"
    assert msg_llm.llm.invocation_success is True
    assert msg_llm.llm.response_generation_success is True
    assert msg_llm.tool.success is True
    assert msg_llm.tool.source == "RISPort70"

    # 2. DETERMINISTIC_FALLBACK when LLM times out
    failing_llm = MagicMock()
    failing_llm.provider_name = "ollama"
    failing_llm.model = "llama3"
    failing_llm.generate.side_effect = httpx.ReadTimeout("timed out")
    agent_fb = CUCMAssistantAgent(
        registry=reg,
        router=IntentRouter(llm_client=failing_llm),
        formatter=ResponseFormatter(llm_client=failing_llm),
        conversations=ConversationManager(),
    )
    msg_fb = agent_fb.ask("How many phones are registered?", session_id="s_fb")
    assert msg_fb.execution_mode == "DETERMINISTIC_FALLBACK"
    assert msg_fb.llm.error_category == "Timeout"
    assert "Deterministic fallback was used successfully" in msg_fb.execution_notice

    # 3. TOOL_ONLY
    msg_tool = agent_fb.ask("How many phones are registered?", session_id="s_tool", execution_preference="tool_only")
    assert msg_tool.execution_mode == "TOOL_ONLY"
    assert msg_tool.llm.invocation_success is False

    # 4. LLM_FAILED when no fallback tool matches
    msg_fail = agent_fb.ask("Tell me a joke about routers", session_id="s_fail")
    assert msg_fail.execution_mode == "LLM_FAILED"
    assert msg_fail.ai_available is False


def test_assistant_phase4_endpoints_and_new_chat():
    client = TestClient(app)
    st = client.get("/api/assistant/status")
    assert st.status_code == 200
    assert "configured_provider" in st.json()

    nc = client.post("/api/assistant/new-chat")
    assert nc.status_code == 200
    sid = nc.json()["session_id"]
    assert sid.startswith("chat-")

    ref = client.post("/api/assistant/refresh")
    assert ref.status_code == 200


def test_floating_ui_controls_and_responsive_css():
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    css = Path("frontend/assets/css/dashboard.css").read_text(encoding="utf-8")
    js = Path("frontend/assets/js/dashboard.js").read_text(encoding="utf-8")

    for marker in ("chatFab", "chatWidget", "chatSettingsPanel", "minimizeChatWidget", "toggleMaximizeChat", "startNewChat", "refreshAssistantStatus", "closeChatWidget"):
        assert marker in html or marker in js

    assert "Manrope" in html and "Manrope" in css
    assert "@media (max-width: 640px)" in css
    assert "127.0.0.1" not in js and "localhost" not in js
