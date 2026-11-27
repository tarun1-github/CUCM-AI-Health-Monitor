"""Tests for custom Netmiko CUCM 'admin:' prompt handling."""

from unittest.mock import MagicMock, patch
import pytest

from backend.connectivity.exceptions import CUCMSSHAuthenticationError, CUCMSSHCommandError
from backend.connectivity.netmiko_client import (
    NetmikoCUCMClient,
    SSHConnectionConfig,
    _build_cucm_connection_class,
)


def test_cucm_connection_class_overrides_ios_config_mode():
    cucm_cls = _build_cucm_connection_class()
    instance = cucm_cls.__new__(cucm_cls)
    assert instance.check_config_mode() is False


def test_cucm_prompt_detection_reads_admin_prompt():
    cucm_cls = _build_cucm_connection_class()
    instance = cucm_cls.__new__(cucm_cls)
    mock_channel = MagicMock()
    mock_channel.recv_ready.return_value = True
    mock_channel.recv.return_value = b"Cisco Unified Communications Manager\r\nadmin:"
    instance.remote_conn = mock_channel
    instance._read_buffer = ""
    instance.banner_timeout = 5

    assert instance._detect_cucm_prompt() == "admin:"


def test_netmiko_client_connect_and_send_command():
    cfg = SSHConnectionConfig(host="10.197.206.141", username="Administrator", password="secret")
    mock_conn = MagicMock()
    mock_conn.base_prompt = "admin:"
    mock_conn.send_command.return_value = "Host Name : cucm-pub"

    with patch("backend.connectivity.netmiko_client._build_cucm_connection_class", return_value=lambda **kw: mock_conn):
        client = NetmikoCUCMClient(cfg)
        client.connect()
        assert client.is_connected() is True
        assert client.get_prompt() == "admin:"

        out = client.send_command("show status")
        assert "cucm-pub" in out
        mock_conn.send_command.assert_called_once()
        client.disconnect()
        assert client.is_connected() is False


def test_netmiko_missing_credentials_raises():
    cfg = SSHConnectionConfig(host="10.197.206.141", username="Administrator", password="<SET_LOCALLY>")
    client = NetmikoCUCMClient(cfg)
    with pytest.raises(CUCMSSHAuthenticationError):
        client.connect()


def test_netmiko_blocks_unapproved_command():
    cfg = SSHConnectionConfig(host="10.197.206.141", username="Administrator", password="secret")
    client = NetmikoCUCMClient(cfg)
    client._connected = True
    client._connection = MagicMock()
    with pytest.raises(CUCMSSHCommandError):
        client.send_command("set cli pagination on")


def test_netmiko_uses_valid_base_connection_host_key_params():
    """Regression test: Netmiko BaseConnection expects ssh_strict/system_host_keys, not use_host_keys."""
    cfg = SSHConnectionConfig(
        host="10.197.206.141",
        username="Administrator",
        password="secret",
        strict_host_key=False,
    )
    captured_kwargs = {}

    def fake_conn(**kwargs):
        captured_kwargs.update(kwargs)
        m = MagicMock()
        m.base_prompt = "admin:"
        return m

    with patch("backend.connectivity.netmiko_client._build_cucm_connection_class", return_value=fake_conn):
        client = NetmikoCUCMClient(cfg)
        client.connect()
        assert "use_host_keys" not in captured_kwargs
        assert captured_kwargs["ssh_strict"] is False
        assert captured_kwargs["system_host_keys"] is False
