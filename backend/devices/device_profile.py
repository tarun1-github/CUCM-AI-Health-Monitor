"""CUCM Server Profile model with IP/FQDN validation and SSRF protection."""

import ipaddress
import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator

_FQDN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$|^[a-zA-Z0-9][a-zA-Z0-9-]{0,62}$")


def validate_cucm_host(host: str) -> str:
    """Validate IPv4/FQDN and block loopback/link-local/unspecified SSRF targets."""
    cleaned = (host or "").strip()
    if not cleaned:
        raise ValueError("CUCM host/IP is required.")
    if cleaned.lower() in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        raise ValueError(f"Loopback/unspecified host '{cleaned}' is not permitted for CUCM.")
    try:
        ip = ipaddress.ip_address(cleaned)
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise ValueError(f"Non-routable/restricted IP '{cleaned}' is not permitted.")
        return str(ip)
    except ValueError as exc:
        if "not permitted" in str(exc):
            raise
        if _FQDN_RE.match(cleaned):
            return cleaned
        raise ValueError(f"Invalid CUCM IP address or FQDN: '{cleaned}'") from exc


class CUCMServerProfile(BaseModel):
    """Persistent non-secret profile for a registered CUCM server."""

    server_id: str
    name: str
    host: str
    cucm_version: str = "15.0"
    role: str = "Publisher"  # Publisher | Subscriber
    axl_port: int = 8443
    ssh_port: int = 22
    axl_username: str = "Administrator"
    axl_credential_ref: str = ""
    cli_username: str = "Administrator"
    cli_credential_ref: str = ""
    verify_ssl: bool = False
    strict_host_key: bool = False
    timeout: int = 30
    enabled: bool = True
    is_default: bool = False
    last_successful_connection: Optional[str] = None
    last_connection_status: Optional[str] = None

    @field_validator("host")
    @classmethod
    def _check_host(cls, v: str) -> str:
        return validate_cucm_host(v)

    @field_validator("axl_port", "ssh_port")
    @classmethod
    def _check_port(cls, v: int) -> int:
        if not (1 <= int(v) <= 65535):
            raise ValueError(f"Port {v} out of range 1..65535")
        return int(v)
