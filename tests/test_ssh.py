"""Tests for environment configuration, read-only validation, and Paramiko SSH client."""

from unittest.mock import MagicMock, patch
import paramiko
import pytest

from backend.connectivity.exceptions import (
    CUCMSSHAuthenticationError,
    CUCMSSHCommandError,
    CUCMSSHHostKeyError,
    CUCMSSHTimeoutError,
)
from backend.connectivity.netmiko_client import SSHConnectionConfig
from backend.connectivity.ssh_client import ParamikoCUCMClient
from backend.utils.config_loader import AppSettings
from backend.utils.validators import validate_readonly_command


def test_production_enforces_ssl_and_host_key():
    s = AppSettings(
        APP_ENV="production",
        CUCM_VERIFY_SSL=False,
        CUCM_SSH_STRICT_HOST_KEY=False,
    )
    assert s.effective_verify_ssl is True
    assert s.effective_strict_host_key is True


def test_readonly_command_validator_blocks_mutations():
    assert validate_readonly_command("show status")[0] is True
    assert validate_readonly_command("utils service restart Cisco Tomcat")[0] is False
    assert validate_readonly_command("set network ip eth0 1.2.3.4")[0] is False
    assert validate_readonly_command("utils system restart")[0] is False


def test_paramiko_connect_and_command_execution():
    cfg = SSHConnectionConfig(
        host="10.197.206.141",
        port=22,
        username="Administrator",
        password="secret",
        strict_host_key=False,
    )
    mock_channel = MagicMock()
    mock_channel.recv_ready.side_effect = [True, True, True]
    mock_channel.recv.side_effect = [
        b"Welcome to Cisco UC\r\nadmin:",
        b"set cli pagination off\r\nadmin:",
        b"show version active\r\nActive Master Version: 15.0.1.10000-14\r\nadmin:",
    ]

    with patch("paramiko.SSHClient") as mock_ssh_cls:
        mock_ssh = mock_ssh_cls.return_value
        mock_ssh.invoke_shell.return_value = mock_channel

        client = ParamikoCUCMClient(cfg)
        client.connect()
        assert client.is_connected() is True
        assert client.get_prompt() == "admin:"

        output = client.send_command("show version active")
        assert "15.0.1.10000-14" in output
        client.disconnect()
        assert client.is_connected() is False


def test_paramiko_auth_failure_raises():
    cfg = SSHConnectionConfig(host="10.197.206.141", username="Administrator", password="bad", strict_host_key=False)
    with patch("paramiko.SSHClient") as mock_ssh_cls:
        mock_ssh_cls.return_value.connect.side_effect = paramiko.AuthenticationException("Auth failed")
        client = ParamikoCUCMClient(cfg)
        with pytest.raises(CUCMSSHAuthenticationError):
            client.connect()


def test_paramiko_rejects_mutating_command():
    cfg = SSHConnectionConfig(host="10.197.206.141", username="Administrator", password="secret", strict_host_key=False)
    client = ParamikoCUCMClient(cfg)
    client._connected = True
    with pytest.raises(CUCMSSHCommandError):
        client.send_command("utils system restart")
