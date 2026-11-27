"""CUCM Device Manager: multi-server onboarding, per-server credentials, target resolution, and connectivity checks."""

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.connectivity.axl_client import AXLConfig, CUCMAXLClient
from backend.connectivity.netmiko_client import NetmikoCUCMClient, SSHConnectionConfig
from backend.connectivity.ssh_client import ParamikoCUCMClient
from backend.devices.credential_manager import CredentialManager
from backend.devices.device_profile import CUCMServerProfile
from backend.devices.device_registry import DeviceRegistry
from backend.integrations.cucm_axl.risport_client import RisPort70Client

_IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


@dataclass
class TargetResolutionResult:
    resolved: bool
    profile: Optional[CUCMServerProfile] = None
    ambiguous: bool = False
    candidates: List[CUCMServerProfile] = field(default_factory=list)
    error: Optional[str] = None
    extracted_target: Optional[str] = None


class CUCMDeviceManager:
    """Manages registered CUCM servers, per-server credentials, and secure target resolution."""

    def __init__(
        self,
        registry: Optional[DeviceRegistry] = None,
        credentials: Optional[CredentialManager] = None,
    ):
        self.registry = registry or DeviceRegistry()
        self.credentials = credentials or CredentialManager()

    def list_servers(self) -> List[Dict[str, Any]]:
        result = []
        for p in self.registry.list_all():
            d = p.model_dump()
            d["has_axl_credentials"] = self.credentials.has_secret(p.axl_credential_ref)
            d["has_cli_credentials"] = self.credentials.has_secret(p.cli_credential_ref)
            result.append(d)
        return result

    def get_server(self, server_id: str) -> Optional[CUCMServerProfile]:
        return self.registry.get(server_id)

    def get_default_server(self) -> Optional[CUCMServerProfile]:
        servers = self.registry.list_all()
        enabled = [s for s in servers if s.enabled]
        for s in enabled:
            if s.is_default:
                return s
        return enabled[0] if enabled else (servers[0] if servers else None)

    def set_default_server(self, server_id: str) -> CUCMServerProfile:
        prof = self.registry.get(server_id)
        if not prof:
            raise ValueError(f"CUCM server '{server_id}' not found.")
        prof.is_default = True
        return self.registry.upsert(prof)

    def add_server(
        self,
        name: str,
        host: str,
        cucm_version: str = "15.0",
        role: str = "Publisher",
        axl_port: int = 8443,
        ssh_port: int = 22,
        axl_username: str = "Administrator",
        axl_password: Optional[str] = None,
        cli_username: str = "Administrator",
        cli_password: Optional[str] = None,
        verify_ssl: bool = False,
        strict_host_key: bool = False,
        timeout: int = 30,
        enabled: bool = True,
        is_default: bool = False,
        server_id: Optional[str] = None,
    ) -> CUCMServerProfile:
        sid = server_id or f"cucm-{uuid.uuid4().hex[:8]}"
        axl_ref = f"secret:{sid}:axl"
        cli_ref = f"secret:{sid}:cli"
        if axl_password:
            self.credentials.store_secret(axl_ref, axl_password)
        if cli_password:
            self.credentials.store_secret(cli_ref, cli_password)
        prof = CUCMServerProfile(
            server_id=sid,
            name=name.strip(),
            host=host.strip(),
            cucm_version=cucm_version.strip(),
            role=role.strip(),
            axl_port=axl_port,
            ssh_port=ssh_port,
            axl_username=axl_username.strip(),
            axl_credential_ref=axl_ref if axl_password else "",
            cli_username=cli_username.strip(),
            cli_credential_ref=cli_ref if cli_password else "",
            verify_ssl=verify_ssl,
            strict_host_key=strict_host_key,
            timeout=timeout,
            enabled=enabled,
            is_default=is_default,
        )
        return self.registry.upsert(prof)

    def update_server(self, server_id: str, updates: Dict[str, Any]) -> CUCMServerProfile:
        prof = self.registry.get(server_id)
        if not prof:
            raise ValueError(f"CUCM server '{server_id}' not found.")
        axl_pw = updates.pop("axl_password", None)
        cli_pw = updates.pop("cli_password", None)
        if axl_pw:
            ref = prof.axl_credential_ref or f"secret:{server_id}:axl"
            if not ref.startswith("env:"):
                self.credentials.store_secret(ref, axl_pw)
                prof.axl_credential_ref = ref
            else:
                new_ref = f"secret:{server_id}:axl"
                self.credentials.store_secret(new_ref, axl_pw)
                prof.axl_credential_ref = new_ref
        if cli_pw:
            ref = prof.cli_credential_ref or f"secret:{server_id}:cli"
            new_ref = ref if not ref.startswith("env:") else f"secret:{server_id}:cli"
            self.credentials.store_secret(new_ref, cli_pw)
            prof.cli_credential_ref = new_ref

        data = prof.model_dump()
        for k, v in updates.items():
            if k in data and v is not None:
                data[k] = v
        updated = CUCMServerProfile(**data)
        return self.registry.upsert(updated)

    def delete_server(self, server_id: str) -> bool:
        prof = self.registry.get(server_id)
        if not prof:
            return False
        self.credentials.delete_secret(prof.axl_credential_ref)
        self.credentials.delete_secret(prof.cli_credential_ref)
        return self.registry.delete(server_id)

    def resolve_target(
        self,
        query: str = "",
        explicit_server_id: Optional[str] = None,
    ) -> TargetResolutionResult:
        """Resolve target CUCM server strictly against the registered inventory."""
        servers = self.registry.list_all()
        if explicit_server_id:
            prof = self.registry.get(explicit_server_id)
            if not prof:
                return TargetResolutionResult(resolved=False, error=f"Selected server '{explicit_server_id}' is not registered.")
            if not prof.enabled:
                return TargetResolutionResult(resolved=False, error=f"CUCM server '{prof.name}' ({prof.host}) is disabled.")
            return TargetResolutionResult(resolved=True, profile=prof, extracted_target=prof.host)

        # A. Check explicit IPv4 in natural-language query
        ip_matches = _IPV4_RE.findall(query or "")
        if ip_matches:
            target_ip = ip_matches[0]
            matched = [s for s in servers if s.host == target_ip]
            if not matched:
                return TargetResolutionResult(
                    resolved=False,
                    extracted_target=target_ip,
                    error=f"Target '{target_ip}' is not in the registered CUCM server inventory. Onboard it in Device Manager first.",
                )
            enabled_matched = [s for s in matched if s.enabled]
            if not enabled_matched:
                return TargetResolutionResult(
                    resolved=False,
                    extracted_target=target_ip,
                    error=f"CUCM server '{target_ip}' is registered but currently disabled.",
                )
            if len(enabled_matched) > 1:
                return TargetResolutionResult(resolved=False, ambiguous=True, candidates=enabled_matched, extracted_target=target_ip)
            return TargetResolutionResult(resolved=True, profile=enabled_matched[0], extracted_target=target_ip)

        # B. Check friendly server name or server_id in query
        ql = (query or "").lower()
        name_matches = [
            s for s in servers
            if s.enabled and (s.name.lower() in ql or s.server_id.lower() in ql or s.host.lower() in ql)
        ]
        if len(name_matches) == 1:
            return TargetResolutionResult(resolved=True, profile=name_matches[0], extracted_target=name_matches[0].name)
        if len(name_matches) > 1:
            return TargetResolutionResult(resolved=False, ambiguous=True, candidates=name_matches)

        # C. Default CUCM server
        default_prof = self.get_default_server()
        if not default_prof:
            return TargetResolutionResult(resolved=False, error="No CUCM servers are registered in Device Manager.")
        if not default_prof.enabled:
            return TargetResolutionResult(resolved=False, error=f"Default CUCM server '{default_prof.name}' is disabled.")
        return TargetResolutionResult(resolved=True, profile=default_prof, extracted_target=default_prof.host)

    def get_axl_config(self, profile: CUCMServerProfile) -> AXLConfig:
        return AXLConfig(
            host=profile.host,
            port=profile.axl_port,
            username=profile.axl_username,
            password=self.credentials.get_secret(profile.axl_credential_ref),
            verify_ssl=profile.verify_ssl,
            axl_version=profile.cucm_version,
            timeout=profile.timeout,
        )

    def get_ssh_config(self, profile: CUCMServerProfile) -> SSHConnectionConfig:
        return SSHConnectionConfig(
            host=profile.host,
            port=profile.ssh_port,
            username=profile.cli_username,
            password=self.credentials.get_secret(profile.cli_credential_ref),
            timeout=profile.timeout,
            strict_host_key=profile.strict_host_key,
        )

    def mark_connected(self, server_id: str, status: str = "Connected") -> None:
        prof = self.registry.get(server_id)
        if prof:
            prof.last_successful_connection = datetime.now(timezone.utc).isoformat()
            prof.last_connection_status = status
            self.registry.upsert(prof)

    def test_ssh(self, server_id: str) -> Dict[str, Any]:
        prof = self.registry.get(server_id)
        if not prof:
            return {"connected": False, "error": f"Server '{server_id}' not found."}
        cfg = self.get_ssh_config(prof)
        try:
            with NetmikoCUCMClient(cfg) as nm:
                self.mark_connected(server_id, "SSH OK (netmiko)")
                return {"connected": True, "transport": "netmiko", "prompt": nm.get_prompt(), "host": prof.host}
        except Exception as nm_err:
            try:
                with ParamikoCUCMClient(cfg) as pm:
                    self.mark_connected(server_id, "SSH OK (paramiko)")
                    return {"connected": True, "transport": "paramiko", "prompt": pm.get_prompt(), "host": prof.host}
            except Exception as pm_err:
                return {"connected": False, "host": prof.host, "error": f"Netmiko: {nm_err} | Paramiko: {pm_err}"}

    def test_axl(self, server_id: str) -> Dict[str, Any]:
        prof = self.registry.get(server_id)
        if not prof:
            return {"connected": False, "error": f"Server '{server_id}' not found."}
        try:
            res = CUCMAXLClient(self.get_axl_config(prof)).test_connection()
            self.mark_connected(server_id, f"AXL OK ({res.get('version')})")
            return res
        except Exception as exc:
            return {"connected": False, "host": prof.host, "error": str(exc)}

    def test_risport(self, server_id: str) -> Dict[str, Any]:
        prof = self.registry.get(server_id)
        if not prof:
            return {"connected": False, "error": f"Server '{server_id}' not found."}
        try:
            res = RisPort70Client(self.get_axl_config(prof)).select_cm_device(device_name="*", max_devices=5)
            self.mark_connected(server_id, "RISPort70 OK")
            return {"connected": True, "host": prof.host, "devices_sampled": len(res.get("devices", []))}
        except Exception as exc:
            return {"connected": False, "host": prof.host, "error": str(exc)}

    def test_perfmon(self, server_id: str) -> Dict[str, Any]:
        prof = self.registry.get(server_id)
        if not prof:
            return {"connected": False, "error": f"Server '{server_id}' not found."}
        from backend.integrations.cucm_perfmon.client import CUCMPerfMonClient
        try:
            pm = CUCMPerfMonClient(self.get_axl_config(prof))
            res = pm.collect_processor_counters()
            self.mark_connected(server_id, "PerfMon OK")
            return {"connected": True, "host": prof.host, "cpu_utilization_pct": res.get("cpu_utilization_pct")}
        except Exception as exc:
            return {"connected": False, "host": prof.host, "error": str(exc)}


_DEVICE_MGR: Optional[CUCMDeviceManager] = None


def get_device_manager() -> CUCMDeviceManager:
    global _DEVICE_MGR
    if _DEVICE_MGR is None:
        _DEVICE_MGR = CUCMDeviceManager()
    return _DEVICE_MGR
