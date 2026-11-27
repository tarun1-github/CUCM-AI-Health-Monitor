"""Netmiko-based SSH client with custom Cisco CUCM 'admin:' prompt handling."""

import time
from dataclasses import dataclass
from typing import Optional

from backend.connectivity.exceptions import (
    CUCMSSHAuthenticationError,
    CUCMSSHCommandError,
    CUCMSSHConnectionError,
    CUCMSSHTimeoutError,
)
from backend.utils.config_loader import get_settings
from backend.utils.logger import get_logger
from backend.utils.validators import validate_readonly_command

logger = get_logger("netmiko", category="ssh")


@dataclass
class SSHConnectionConfig:
    host: str
    port: int = 22
    username: str = ""
    password: str = ""
    timeout: int = 30
    command_timeout: int = 60
    prompt_timeout: int = 15
    known_hosts_file: Optional[str] = None
    strict_host_key: bool = True

    @classmethod
    def from_settings(cls, host: Optional[str] = None, port: Optional[int] = None) -> "SSHConnectionConfig":
        settings = get_settings()
        password = (
            settings.cucm_cli_password.get_secret_value()
            if settings.cucm_cli_password
            else ""
        )
        return cls(
            host=host or settings.cucm_host,
            port=port or settings.cucm_ssh_port,
            username=settings.cucm_cli_username,
            password=password,
            timeout=settings.cucm_ssh_timeout,
            command_timeout=settings.cucm_command_timeout,
            prompt_timeout=settings.cucm_prompt_timeout,
            known_hosts_file=settings.cucm_ssh_known_hosts or None,
            strict_host_key=settings.effective_strict_host_key,
        )


def _build_cucm_connection_class():
    """Build custom Netmiko connection class for CUCM 'admin:' CLI prompt."""
    from netmiko.cisco.cisco_ios import CiscoIosSSH

    class CUCMConnection(CiscoIosSSH):
        """Custom Netmiko handler for Cisco CUCM Administrator CLI.

        Standard CiscoIosSSH fails on CUCM because:
        1. CUCM presents 'admin:' instead of IOS '#' or '>'.
        2. CUCM has no IOS config mode and rejects 'terminal length 0'.
        3. CUCM uses 'set cli pagination off' and 'quit' to exit.
        """

        def session_preparation(self) -> None:
            self.base_prompt = self._detect_cucm_prompt()
            if not self.base_prompt:
                raise CUCMSSHTimeoutError("Failed to detect CUCM 'admin:' prompt after SSH login.")
            self.base_prompt = "admin:"
            self.disable_paging(command="set cli pagination off")
            verified = self._detect_cucm_prompt()
            if verified:
                self.base_prompt = "admin:"
            logger.info("CUCM Netmiko session prepared. Prompt=%s", self.base_prompt)

        def _detect_cucm_prompt(self) -> str:
            if not self.remote_conn:
                return ""
            channel = self.remote_conn
            output = getattr(self, "_read_buffer", "") or ""
            self._read_buffer = ""

            if output.strip().endswith("admin:"):
                return "admin:"

            start_time = time.monotonic()
            timeout = getattr(self, "banner_timeout", 15) or 15
            while time.monotonic() - start_time < timeout:
                if channel.recv_ready():
                    chunk = channel.recv(4096).decode("utf-8", errors="ignore")
                    output += chunk
                    if output.strip().endswith("admin:"):
                        return "admin:"
                    for line in reversed(output.strip().splitlines()):
                        if line.strip() == "admin:":
                            return "admin:"
                else:
                    time.sleep(0.1)
            return ""

        def check_config_mode(
            self,
            check_string: str = ")#",
            pattern: str = r"[>#]",
            force_regex: bool = False,
        ) -> bool:
            return False

        def set_base_prompt(
            self,
            pri_prompt_terminator: str = "#",
            alt_prompt_terminator: str = ">",
            delay_factor: float = 1.0,
            pattern: Optional[str] = None,
        ) -> str:
            if pri_prompt_terminator == "#" and alt_prompt_terminator == ">" and pattern is None:
                return self._detect_cucm_prompt()
            return super().set_base_prompt(
                pri_prompt_terminator=pri_prompt_terminator,
                alt_prompt_terminator=alt_prompt_terminator,
                delay_factor=delay_factor,
                pattern=pattern,
            )

        def cleanup(self, command: str = "quit") -> None:
            try:
                self.write_channel("quit\n")
            except Exception:
                pass

    return CUCMConnection


class NetmikoCUCMClient:
    """Managed Netmiko client for read-only CUCM CLI execution."""

    def __init__(self, config: Optional[SSHConnectionConfig] = None):
        self.config = config or SSHConnectionConfig.from_settings()
        self._connection = None
        self._connected = False
        self._prompt = ""

    def connect(self) -> None:
        if not self.config.username or not self.config.password or self.config.password == "<SET_LOCALLY>":
            raise CUCMSSHAuthenticationError("CUCM CLI credentials are not configured.")

        try:
            from netmiko.exceptions import (
                NetmikoAuthenticationException,
                NetmikoTimeoutException,
                SSHException,
            )
        except ImportError as exc:
            raise CUCMSSHConnectionError("Netmiko is not installed.") from exc

        cucm_cls = _build_cucm_connection_class()
        device_params = {
            "device_type": "cisco_ios",
            "host": self.config.host,
            "port": self.config.port,
            "username": self.config.username,
            "password": self.config.password,
            "timeout": self.config.timeout,
            "session_timeout": self.config.command_timeout,
            "auth_timeout": self.config.timeout,
            "banner_timeout": self.config.prompt_timeout,
            "conn_timeout": self.config.timeout,
            "global_delay_factor": 1.2,
            "fast_cli": False,
            "allow_agent": False,
            "use_keys": False,
            "ssh_strict": self.config.strict_host_key,
            "system_host_keys": self.config.strict_host_key,
        }
        if self.config.known_hosts_file:
            device_params["ssh_strict"] = True
            device_params["system_host_keys"] = False
            device_params["alt_host_keys"] = True
            device_params["alt_key_file"] = self.config.known_hosts_file

        try:
            logger.info("Connecting via Netmiko to %s:%d", self.config.host, self.config.port)
            self._connection = cucm_cls(**device_params)
            self._connected = True
            self._prompt = getattr(self._connection, "base_prompt", "admin:")
            logger.info("Netmiko connected to %s (prompt=%s)", self.config.host, self._prompt)
        except NetmikoAuthenticationException as exc:
            logger.error("Netmiko SSH authentication failed for %s", self.config.username)
            raise CUCMSSHAuthenticationError(f"SSH authentication failed for {self.config.username}") from exc
        except NetmikoTimeoutException as exc:
            logger.error("Netmiko SSH timeout connecting to %s:%d", self.config.host, self.config.port)
            raise CUCMSSHTimeoutError(f"SSH connection timeout to {self.config.host}:{self.config.port}") from exc
        except SSHException as exc:
            logger.error("Netmiko SSH protocol error: %s", exc)
            raise CUCMSSHConnectionError(f"SSH protocol error: {exc}") from exc
        except CUCMSSHTimeoutError:
            raise
        except Exception as exc:
            logger.error("Unexpected Netmiko connection error: %s", exc)
            raise CUCMSSHConnectionError(f"SSH connection failed: {exc}") from exc

    def is_connected(self) -> bool:
        return self._connected and self._connection is not None

    def get_prompt(self) -> str:
        return self._prompt

    def send_command(self, command: str, timeout: Optional[int] = None) -> str:
        allowed, reason = validate_readonly_command(command)
        if not allowed:
            raise CUCMSSHCommandError(reason)

        if not self.is_connected():
            raise CUCMSSHConnectionError("Not connected to CUCM via Netmiko.")

        read_timeout = timeout or self.config.command_timeout
        try:
            logger.info("Executing command (Netmiko): %s", command)
            output = self._connection.send_command(
                command,
                expect_string=r"admin:",
                read_timeout=read_timeout,
                strip_prompt=True,
                strip_command=True,
            )
            return output
        except Exception as exc:
            logger.error("Netmiko command '%s' failed: %s", command, exc)
            raise CUCMSSHCommandError(f"Command '{command}' failed: {exc}") from exc

    def disconnect(self) -> None:
        if self._connection and self._connected:
            try:
                self._connection.disconnect()
                logger.info("Netmiko SSH session closed for %s", self.config.host)
            except Exception as exc:
                logger.warning("Error closing Netmiko session: %s", exc)
            finally:
                self._connected = False
                self._connection = None

    def __enter__(self) -> "NetmikoCUCMClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()
