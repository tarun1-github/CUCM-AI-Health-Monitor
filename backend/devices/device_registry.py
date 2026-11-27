"""Persistent CUCM server inventory registry (metadata only; secrets stored via CredentialManager)."""

import json
import threading
from pathlib import Path
from typing import Dict, List, Optional

from backend.devices.device_profile import CUCMServerProfile
from backend.utils.config_loader import PROJECT_ROOT, get_settings, load_devices_yaml

_REGISTRY_FILE = PROJECT_ROOT / "data" / "devices" / "registry.json"


class DeviceRegistry:
    """Thread-safe JSON-backed registry of CUCMServerProfile records."""

    def __init__(self, registry_file: Optional[Path] = None):
        self._lock = threading.RLock()
        self._file = registry_file or _REGISTRY_FILE
        self._profiles: Dict[str, CUCMServerProfile] = {}
        self._load_or_seed()

    def _load_or_seed(self) -> None:
        with self._lock:
            if self._file.exists():
                try:
                    raw = json.loads(self._file.read_text(encoding="utf-8"))
                    for item in raw.get("servers", []):
                        prof = CUCMServerProfile(**item)
                        self._profiles[prof.server_id] = prof
                except Exception:
                    self._profiles = {}

            if not self._profiles:
                self._seed_from_config()
                self._save()

    def _seed_from_config(self) -> None:
        s = get_settings()
        yaml_devs = load_devices_yaml()
        if yaml_devs:
            for idx, d in enumerate(yaml_devs):
                sid = d.get("id") or f"cucm-{idx + 1}"
                self._profiles[sid] = CUCMServerProfile(
                    server_id=sid,
                    name=d.get("name") or "CUCM-PUB-01",
                    host=d.get("host") or s.cucm_host,
                    cucm_version=s.cucm_axl_version or "15.0",
                    role=d.get("role") or "Publisher",
                    axl_port=int(d.get("axl_port") or s.cucm_port),
                    ssh_port=int(d.get("ssh_port") or s.cucm_ssh_port),
                    axl_username=s.cucm_username,
                    axl_credential_ref="env:axl",
                    cli_username=s.cucm_cli_username,
                    cli_credential_ref="env:cli",
                    verify_ssl=s.effective_verify_ssl,
                    strict_host_key=s.effective_strict_host_key,
                    timeout=s.cucm_ssh_timeout,
                    enabled=bool(d.get("enabled", True)),
                    is_default=(idx == 0),
                )
        else:
            self._profiles["cucm-primary"] = CUCMServerProfile(
                server_id="cucm-primary",
                name="CUCM-PUB-01",
                host=s.cucm_host,
                cucm_version=s.cucm_axl_version or "15.0",
                role="Publisher",
                axl_port=s.cucm_port,
                ssh_port=s.cucm_ssh_port,
                axl_username=s.cucm_username,
                axl_credential_ref="env:axl",
                cli_username=s.cucm_cli_username,
                cli_credential_ref="env:cli",
                verify_ssl=s.effective_verify_ssl,
                strict_host_key=s.effective_strict_host_key,
                timeout=s.cucm_ssh_timeout,
                enabled=True,
                is_default=True,
            )

    def _save(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"servers": [p.model_dump() for p in self._profiles.values()]}
        self._file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list_all(self) -> List[CUCMServerProfile]:
        with self._lock:
            return [p.model_copy() for p in self._profiles.values()]

    def get(self, server_id: str) -> Optional[CUCMServerProfile]:
        with self._lock:
            p = self._profiles.get(server_id)
            return p.model_copy() if p else None

    def upsert(self, profile: CUCMServerProfile) -> CUCMServerProfile:
        with self._lock:
            if profile.is_default:
                for p in self._profiles.values():
                    p.is_default = False
            elif not any(p.is_default for p in self._profiles.values() if p.server_id != profile.server_id):
                profile.is_default = True
            self._profiles[profile.server_id] = profile
            self._save()
            return profile.model_copy()

    def delete(self, server_id: str) -> bool:
        with self._lock:
            if server_id not in self._profiles:
                return False
            was_default = self._profiles[server_id].is_default
            del self._profiles[server_id]
            if was_default and self._profiles:
                first = next(iter(self._profiles.values()))
                first.is_default = True
            self._save()
            return True
