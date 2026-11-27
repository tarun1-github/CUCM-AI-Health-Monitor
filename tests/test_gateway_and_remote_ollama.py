"""Tests for hierarchical LLMGateway (Gemini -> OpenRouter -> Remote Ollama) and provenance."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from backend.agents.conversation_manager import ConversationManager
from backend.agents.cucm_agent import CUCMAssistantAgent
from backend.agents.intent_router import IntentRouter
from backend.agents.response_formatter import ResponseFormatter
from backend.ai.gateway import LLMGatewayClient
from backend.ai.ollama_client import OllamaLLMClient
from backend.ai.openrouter_client import OpenRouterLLMClient
from backend.ai.provider_manager import LLMProviderManager
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry
from backend.tools import register_all_cucm_tools
from backend.utils.config_loader import get_settings


def test_remote_ollama_default_endpoint_is_10_106_44_12():
    ol = OllamaLLMClient()
    assert ol.base_url == "http://10.106.44.12:11434"
    assert "localhost" not in ol.base_url and "127.0.0.1" not in ol.base_url
    get_settings.cache_clear()
    s = get_settings()
    assert s.ollama_base_url == "http://10.106.44.12:11434"


def test_gateway_gemini_primary_success():
    gemini = MagicMock()
    gemini.model = "gemini-3.1-flash-lite"
    gemini.generate.return_value = '{"ok": true}'

    gw = LLMGatewayClient(gemini=gemini, openrouter=MagicMock(), ollama=MagicMock(), max_retries=0)
    out = gw.generate("sys", "user")
    assert out == '{"ok": true}'
    assert gw.last_used_provider == "gemini"
    assert gw.last_used_model == "gemini-3.1-flash-lite"
    assert gw.fallback_used is False


def test_gateway_gemini_fails_openrouter_model_iteration_succeeds():
    gemini = MagicMock()
    gemini.model = "gemini-3.1-flash-lite"
    gemini.generate.side_effect = httpx.ReadTimeout("Gemini timeout")

    or_client = OpenRouterLLMClient(
        api_key="sk-or-v1-dummy",
        models=["liquid/lfm-2.5-2.6b:free", "google/gemma-4-26b-a4b-it:free"],
    )
    calls = []

    def fake_or_gen(sys_p, usr_p, json_mode=True):
        calls.append(or_client.model)
        if or_client.model == "liquid/lfm-2.5-2.6b:free":
            raise RuntimeError("429 rate limit")
        return "OpenRouter OK"

    or_client.generate = fake_or_gen
    gw = LLMGatewayClient(
        gemini=gemini,
        openrouter=or_client,
        ollama=MagicMock(),
        openrouter_models=["liquid/lfm-2.5-2.6b:free", "google/gemma-4-26b-a4b-it:free"],
        max_retries=0,
    )
    out = gw.generate("sys", "user")
    assert out == "OpenRouter OK"
    assert calls == ["liquid/lfm-2.5-2.6b:free", "google/gemma-4-26b-a4b-it:free"]
    assert gw.last_used_provider == "openrouter"
    assert gw.last_used_model == "google/gemma-4-26b-a4b-it:free"
    assert gw.fallback_used is True
    assert "Gemini" in (gw.original_failure or "")


def test_gateway_openrouter_fails_remote_ollama_succeeds():
    gemini = MagicMock()
    gemini.model = "gemini-3.1-flash-lite"
    gemini.generate.side_effect = RuntimeError("401 Unauthorized")  # permanent -> no retry

    or_client = MagicMock()
    or_client.generate.side_effect = RuntimeError("429 rate limit")

    ollama = MagicMock()
    ollama.model = "llama3"
    ollama.generate.return_value = "Ollama OK"

    gw = LLMGatewayClient(
        gemini=gemini,
        openrouter=or_client,
        ollama=ollama,
        openrouter_models=["liquid/lfm-2.5-2.6b:free"],
        max_retries=2,
    )
    out = gw.generate("sys", "user")
    assert out == "Ollama OK"
    assert gemini.generate.call_count == 1  # permanent 401 did not retry
    assert gw.last_used_provider == "ollama"
    assert gw.last_used_model == "llama3"
    assert gw.fallback_used is True


def test_gateway_all_providers_fail_raises_without_mock():
    gemini = MagicMock()
    gemini.model = "gemini-3.1-flash-lite"
    gemini.generate.side_effect = RuntimeError("401 Invalid API key")

    or_client = MagicMock()
    or_client.generate.side_effect = RuntimeError("401 Invalid API key")

    ollama = MagicMock()
    ollama.model = "llama3"
    ollama.generate.side_effect = httpx.ConnectError("Connection refused")

    gw = LLMGatewayClient(gemini=gemini, openrouter=or_client, ollama=ollama, openrouter_models=["m1"], max_retries=0)
    with pytest.raises(RuntimeError, match="All configured LLM providers failed"):
        gw.generate("sys", "user")
    assert gw.last_used_provider is None


def test_remote_ollama_tags_and_inference_via_provider_manager():
    mgr = LLMProviderManager()
    mgr.configure("ollama", "llama3")
    mock_tags = MagicMock()
    mock_tags.json.return_value = {"models": [{"name": "llama3:latest"}]}
    mock_tags.raise_for_status = MagicMock()
    with patch("httpx.Client") as mc:
        mc.return_value.__enter__.return_value.get.return_value = mock_tags
        conn = mgr.test_connection()
        assert conn["connected"] is True
        assert conn["endpoint"] == "http://10.106.44.12:11434"


def test_agent_reports_actual_fallback_provider_and_model():
    reg = AXLToolRegistry()
    register_all_cucm_tools(reg)
    reg.register(
        reg.get_spec("cucm_get_phone_registration"),
        lambda p: {"total_found": 1, "registered_count": 1, "unregistered_count": 0, "rows": [{"name": "SEP1", "status": "Registered"}]},
    )

    gemini = MagicMock()
    gemini.model = "gemini-3.1-flash-lite"
    gemini.generate.side_effect = httpx.ReadTimeout("Gemini timeout")

    or_client = OpenRouterLLMClient(api_key="sk-or-v1-dummy", models=["google/gemma-4-26b-a4b-it:free"])
    or_client.generate = MagicMock(
        side_effect=[
            '{"tool_name": "cucm_get_phone_registration", "params": {"status": "Any"}, "reason": "reg"}',
            "Your CUCM cluster has 1 registered phone.",
        ]
    )

    gw = LLMGatewayClient(
        gemini=gemini,
        openrouter=or_client,
        ollama=MagicMock(),
        openrouter_models=["google/gemma-4-26b-a4b-it:free"],
        max_retries=0,
    )
    agent = CUCMAssistantAgent(
        registry=reg,
        router=IntentRouter(llm_client=gw),
        formatter=ResponseFormatter(llm_client=gw),
        conversations=ConversationManager(),
    )
    reply = agent.ask("How many phones are registered?", session_id="gw_test")
    assert reply.execution_mode == "LLM_GENERATED"
    assert reply.llm.provider == "openrouter"
    assert reply.llm.model == "google/gemma-4-26b-a4b-it:free"
    assert reply.llm.fallback_used is True
    assert "Gemini" in (reply.llm.original_failure or "")
