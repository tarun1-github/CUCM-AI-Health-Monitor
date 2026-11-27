"""Paramiko-based SSH transport fallback for CUCM CLI."""

import hashlib
import time
from pathlib import Path
from typing import Optional

import paramiko

from backend.connectivity.exceptions import (
    CUCMSSHAuthenticationError,
    CUCMSSHCommandError,
    CUCMSSHConnectionError,
    CUCMSSHHostKeyError,
    CUCMSSHTimeoutError,
)
from backend.connectivity.netmiko_client import SSHConnectionConfig
from backend.utils.config_loader import get_settings
from backend.utils.logger import get_logger
from backend.utils.validators import validate_readonly_command

logger = get_logger("paramiko", category="ssh")


class FingerprintVerifyPolicy(paramiko.MissingHostKeyPolicy):
    """Verify server host key against an explicitly provisioned SHA-256 fingerprint."""

    def __init__(self, expected_fingerprint: str):
        self.expected_fingerprint = expected_fingerprint.strip().lower().removeprefix("sha256:")

    def missing_host_key(self, client, hostname, key):
        digest = hashlib.sha256(key.asbytes()).hexdigest().lower()
        if digest != self.expected_fingerprint:
            raise CUCMSSHHostKeyError(
                f"SSH host key fingerprint mismatch for {hostname}: got sha256:{digest}"
            )


class ParamikoCUCMClient:
    """Direct Paramiko interactive shell client for Cisco CUCM 'admin:' CLI."""

    PROMPT = "admin:"

    def __init__(
        self,
        config: Optional[SSHConnectionConfig] = None,
        host_key_fingerprint: Optional[str] = None,
    ):
        self.config = config or SSHConnectionConfig.from_settings()
        settings = get_settings()
        self.host_key_fingerprint = host_key_fingerprint or settings.cucm_ssh_host_key_fingerprint
        self._client: Optional[paramiko.SSHClient] = None
        self._channel = None
        self._connected = False
        self._prompt = ""

    def _configure_host_key_policy(self, client: paramiko.SSHClient) -> None:
        if self.config.known_hosts_file and Path(self.config.known_hosts_file).exists():
            client.load_host_keys(self.config.known_hosts_file)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        elif self.host_key_fingerprint:
            client.set_missing_host_key_policy(FingerprintVerifyPolicy(self.host_key_fingerprint))
        elif self.config.strict_host_key:
            client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            # Development-only fallback when strict_host_key is explicitly false
            logger.warning("Strict SSH host key checking disabled (development mode only).")
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def connect(self) -> None:
        if not self.config.username or not self.config.password or self.config.password == "<SET_LOCALLY>":
            raise CUCMSSHAuthenticationError("CUCM CLI credentials are not configured.")

        client = paramiko.SSHClient()
        self._configure_host_key_policy(client)

        try:
            logger.info("Connecting via Paramiko to %s:%d", self.config.host, self.config.port)
            client.connect(
                hostname=self.config.host,
                port=self.config.port,
                username=self.config.username,
                password=self.config.password,
                timeout=self.config.timeout,
                banner_timeout=self.config.prompt_timeout,
                auth_timeout=self.config.timeout,
                allow_agent=False,
                look_for_keys=False,
            )
            self._client = client
            self._channel = client.invoke_shell(width=200, height=50)
            self._channel.settimeout(self.config.command_timeout)

            self._wait_for_prompt(timeout=self.config.prompt_timeout)
            self._prompt = self.PROMPT
            self._connected = True

            # Disable pagination so multi-page outputs are not truncated
            self._raw_send("set cli pagination off", timeout=self.config.prompt_timeout)
            logger.info("Paramiko connected to %s (prompt=%s)", self.config.host, self._prompt)

        except CUCMSSHHostKeyError:
            client.close()
            raise
        except paramiko.AuthenticationException as exc:
            client.close()
            raise CUCMSSHAuthenticationError(f"Paramiko authentication failed for {self.config.username}") from exc
        except paramiko.SSHException as exc:
            client.close()
            if "not found in known_hosts" in str(exc).lower() or "reject" in str(exc).lower():
                raise CUCMSSHHostKeyError(f"SSH host key rejected: {exc}") from exc
            raise CUCMSSHConnectionError(f"Paramiko SSH error: {exc}") from exc
        except TimeoutError as exc:
            client.close()
            raise CUCMSSHTimeoutError(f"Paramiko SSH timeout to {self.config.host}:{self.config.port}") from exc
        except CUCMSSHTimeoutError:
            client.close()
            raise
        except Exception as exc:
            client.close()
            raise CUCMSSHConnectionError(f"Paramiko connection failed: {exc}") from exc

    def _wait_for_prompt(self, timeout: int) -> str:
        if not self._channel:
            raise CUCMSSHConnectionError("SSH channel is not open.")
        buffer = ""
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if self._channel.recv_ready():
                chunk = self._channel.recv(4096).decode("utf-8", errors="ignore")
                buffer += chunk
                if buffer.rstrip().endswith(self.PROMPT):
                    return buffer
            else:
                time.sleep(0.1)
        raise CUCMSSHTimeoutError(f"Timed out waiting for CUCM '{self.PROMPT}' prompt after {timeout}s.")

    def _raw_send(self, command: str, timeout: int) -> str:
        if not self._channel:
            raise CUCMSSHConnectionError("SSH channel is not open.")
        self._channel.send(command + "\n")
        raw = self._wait_for_prompt(timeout=timeout)
        lines = raw.splitlines()
        # Strip echoed command and trailing prompt
        if lines and command in lines[0]:
            lines = lines[1:]
        if lines and lines[-1].strip().endswith(self.PROMPT):
            lines = lines[:-1]
        return "\n".join(lines).strip()

    def is_connected(self) -> bool:
        return self._connected and self._client is not None and self._channel is not None

    def get_prompt(self) -> str:
        return self._prompt

    def send_command(self, command: str, timeout: Optional[int] = None) -> str:
        allowed, reason = validate_readonly_command(command)
        if not allowed:
            raise CUCMSSHCommandError(reason)
        if not self.is_connected():
            raise CUCMSSHConnectionError("Not connected to CUCM via Paramiko.")
        try:
            logger.info("Executing command (Paramiko): %s", command)
            return self._raw_send(command, timeout=timeout or self.config.command_timeout)
        except CUCMSSHTimeoutError:
            raise
        except Exception as exc:
            raise CUCMSSHCommandError(f"Command '{command}' failed: {exc}") from exc

    def disconnect(self) -> None:
        if self._channel:
            try:
                self._channel.send("quit\n")
                self._channel.close()
            except Exception:
                pass
            self._channel = None
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._connected = False
        logger.info("Paramiko SSH session closed for %s", self.config.host)

    def __enter__(self) -> "ParamikoCUCMClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()
