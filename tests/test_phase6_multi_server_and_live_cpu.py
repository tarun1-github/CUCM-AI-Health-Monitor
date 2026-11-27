"""Tests for Phase 6 Multi-Server CUCM Device Manager, PerfMon, Dynamic Target Resolution, and Live CPU."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.agents.conversation_manager import ConversationManager
from backend.agents.cucm_agent import CUCMAssistantAgent
from backend.agents.intent_router import IntentRouter
from backend.agents.response_formatter import ResponseFormatter
from backend.connectivity.axl_client import AXLConfig
from backend.devices.credential_manager import CredentialManager
from backend.devices.device_manager import CUCMDeviceManager
from backend.devices.device_profile import validate_cucm_host
from backend.devices.device_registry import DeviceRegistry
from backend.integrations.cucm_axl.tool_registry import AXLToolRegistry
from backend.integrations.cucm_perfmon.client import CUCMPerfMonClient
from backend.main import app
from backend.tools.live_health_tools import register_live_health_tools


def test_ssrf_host_validation():
    assert validate_cucm_host("10.197.206.141") == "10.197.206.141"
    with pytest.raises(ValueError):
        validate_cucm_host("127.0.0.1")
    with pytest.raises(ValueError):
        validate_cucm_host("169.254.169.254")


def test_multi_server_credentials_encrypted_and_target_resolution(tmp_path):
    creds = CredentialManager(secrets_dir=tmp_path)
    reg = DeviceRegistry(registry_file=tmp_path / "registry.json")
    dm = CUCMDeviceManager(registry=reg, credentials=creds)

    sub = dm.add_server(
        name="CUCM-SUB-01",
        host="10.197.206.142",
        role="Subscriber",
        axl_username="axl_sub",
        axl_password="SubAxlSecret!123",
        cli_username="cli_sub",
        cli_password="SubCliSecret!456",
    )

    # Secrets must NEVER appear in plaintext registry.json
    reg_text = (tmp_path / "registry.json").read_text(encoding="utf-8")
    assert "SubAxlSecret!123" not in reg_text
    assert "SubCliSecret!456" not in reg_text

    # Per-server credential isolation
    assert dm.get_axl_config(sub).password == "SubAxlSecret!123"
    assert dm.get_ssh_config(sub).password == "SubCliSecret!456"

    # Dynamic target resolution by IP and friendly name
    r_ip = dm.resolve_target("Check CPU utilization of 10.197.206.142")
    assert r_ip.resolved and r_ip.profile.server_id == sub.server_id

    r_name = dm.resolve_target("Show services on CUCM-SUB-01")
    assert r_name.resolved and r_name.profile.host == "10.197.206.142"

    # Unknown IP rejected
    r_unk = dm.resolve_target("Check CPU of 10.197.206.250")
    assert not r_unk.resolved
    assert "not in the registered CUCM server inventory" in r_unk.error


def test_perfmon_soap_parser_and_ssh_fallback():
    soap_xml = """<?xml version="1.0" encoding="utf-8"?>
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
      <soapenv:Body>
        <ns1:perfmonCollectCounterDataResponse xmlns:ns1="http://schemas.cisco.com/ast/soap">
          <ns1:perfmonCollectCounterDataReturn>
            <ns1:item><ns1:Name>\\\\10.197.206.141\\Processor(_Total)\\% CPU Time</ns1:Name><ns1:Value>24.5</ns1:Value></ns1:item>
            <ns1:item><ns1:Name>\\\\10.197.206.141\\Processor(_Total)\\Idle Percentage</ns1:Name><ns1:Value>75.5</ns1:Value></ns1:item>
            <ns1:item><ns1:Name>\\\\10.197.206.141\\Processor(_Total)\\System Percentage</ns1:Name><ns1:Value>14.0</ns1:Value></ns1:item>
          </ns1:perfmonCollectCounterDataReturn>
        </ns1:perfmonCollectCounterDataResponse>
      </soapenv:Body>
    </soapenv:Envelope>"""
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=soap_xml))
    pm = CUCMPerfMonClient(AXLConfig(host="10.197.206.141", username="Administrator", password="pw"), transport=transport)
    res = pm.collect_processor_counters()
    assert res["cpu_utilization_pct"] == 24.5
    assert res["cpu_idle_pct"] == 75.5
    assert res["system_pct"] == 14.0


def test_live_cpu_tool_falls_back_to_ssh_and_agent_routes_live_first(tmp_path):
    creds = CredentialManager(secrets_dir=tmp_path)
    reg_store = DeviceRegistry(registry_file=tmp_path / "registry.json")
    dm = CUCMDeviceManager(registry=reg_store, credentials=creds)

    tool_reg = AXLToolRegistry()
    register_live_health_tools(tool_reg, device_manager=dm)

    with patch("backend.tools.live_health_tools.CUCMPerfMonClient") as mock_pm, \
         patch("backend.tools.live_health_tools._run_ssh_commands") as mock_ssh:
        mock_pm.return_value.collect_processor_counters.side_effect = RuntimeError("PerfMon unavailable")
        mock_ssh.return_value = (
            {
                "show perf query class Processor": "_Total -> % CPU Time = 21\n_Total -> Idle Percentage = 79\n",
                "show status": "Host Name : UCM15-HQ-PUB\nCPU Idle: 79.0%\n",
                "show version active": "Active Master Version: 15.0.1.12900-234\n",
            },
            "SSH (Netmiko)",
        )
        llm = MagicMock()
        llm.provider_name = "gemini"
        llm.model = "gemini-3.1-flash-lite"
        llm.generate.side_effect = [
            '{"intent_type": "CUCM_OPERATION", "tool_name": "cucm_live_cpu_utilization", "params": {}}',
            "Successfully connected to CUCM 10.197.206.141. CPU utilization is 21.0% (HEALTHY).",
        ]
        with patch("backend.devices.device_manager.get_device_manager", return_value=dm):
            agent = CUCMAssistantAgent(tool_reg, IntentRouter(llm), ResponseFormatter(llm), ConversationManager())
            reply = agent.ask("Can you check CPU utilization of CUCM 10.197.206.141?", session_id="p6")
        assert reply.execution_mode == "LLM_GENERATED"
        assert reply.tool.name == "cucm_live_cpu_utilization"
        assert reply.tool.success is True
        assert "21.0%" in reply.content


def test_devices_api_crud():
    client = TestClient(app)
    r = client.get("/api/devices")
    assert r.status_code == 200
    assert any(d["host"] == "10.197.206.141" for d in r.json())
