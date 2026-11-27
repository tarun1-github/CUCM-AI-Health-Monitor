"""Encrypted per-server credential storage (never stores plaintext secrets in JSON/YAML)."""

import json
import threading
from pathlib import Path
from typing import Dict, Optional

from cryptography.fernet import Fernet

from backend.utils.config_loader import PROJECT_ROOT, get_settings

_SECRETS_DIR = PROJECT_ROOT / "data" / "devices"
_KEY_FILE = _SECRETS_DIR / ".fernet.key"
_VAULT_FILE = _SECRETS_DIR / "credentials.enc"


class CredentialManager:
    """Manages per-server AXL and CLI credentials using Fernet encryption or .env references."""

    def __init__(self, secrets_dir: Optional[Path] = None):
        self._lock = threading.RLock()
        self._dir = secrets_dir or _SECRETS_DIR
        self._key_file = self._dir / ".fernet.key"
        self._vault_file = self._dir / "credentials.enc"

    def _get_fernet(self) -> Fernet:
        self._dir.mkdir(parents=True, exist_ok=True)
        if self._key_file.exists():
            key = self._key_file.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            self._key_file.write_bytes(key)
        return Fernet(key)

    def _load_vault(self) -> Dict[str, str]:
        if not self._vault_file.exists():
            return {}
        try:
            f = self._get_fernet()
            raw = f.decrypt(self._vault_file.read_bytes())
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_vault(self, data: Dict[str, str]) -> None:
        f = self._get_fernet()
        token = f.encrypt(json.dumps(data).encode("utf-8"))
        self._vault_file.write_bytes(token)

    def store_secret(self, ref: str, secret_value: str) -> None:
        if not secret_value or ref.startswith("env:"):
            return
        with self._lock:
            vault = self._load_vault()
            vault[ref] = secret_value
            self._save_vault(vault)

    def delete_secret(self, ref: str) -> None:
        if not ref or ref.startswith("env:"):
            return
        with self._lock:
            vault = self._load_vault()
            if ref in vault:
                del vault[ref]
                self._save_vault(vault)

    def get_secret(self, ref: str) -> str:
        if not ref:
            return ""
        if ref == "env:axl":
            s = get_settings()
            val = s.cucm_password.get_secret_value() if s.cucm_password else ""
            return "" if val == "<SET_LOCALLY>" else val
        if ref == "env:cli":
            s = get_settings()
            val = s.cucm_cli_password.get_secret_value() if s.cucm_cli_password else ""
            return "" if val == "<SET_LOCALLY>" else val
        with self._lock:
            return self._load_vault().get(ref, "")

    def has_secret(self, ref: str) -> bool:
        return bool(self.get_secret(ref))
