"""Configuration loader for environment variables and YAML config files."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


class AppSettings(BaseSettings):
    """Strongly-typed application settings loaded from .env and environment."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8020, alias="APP_PORT")
    dashboard_public_url: str = Field(default="http://10.106.44.24:8020", alias="DASHBOARD_PUBLIC_URL")

    # CUCM AXL Credentials (HTTPS SOAP API only)
    cucm_host: str = Field(default="10.197.206.141", alias="CUCM_HOST")
    cucm_port: int = Field(default=8443, alias="CUCM_PORT")
    cucm_username: str = Field(default="Administrator", alias="CUCM_USERNAME")
    cucm_password: Optional[SecretStr] = Field(default=None, alias="CUCM_PASSWORD")
    cucm_verify_ssl: bool = Field(default=False, alias="CUCM_VERIFY_SSL")
    cucm_axl_version: str = Field(default="15.0", alias="CUCM_AXL_VERSION")

    # CUCM CLI Credentials (SSH admin: CLI only)
    cucm_cli_username: str = Field(default="Administrator", alias="CUCM_CLI_USERNAME")
    cucm_cli_password: Optional[SecretStr] = Field(default=None, alias="CUCM_CLI_PASSWORD")
    cucm_ssh_port: int = Field(default=22, alias="CUCM_SSH_PORT")
    cucm_ssh_timeout: int = Field(default=30, alias="CUCM_SSH_TIMEOUT")
    cucm_command_timeout: int = Field(default=60, alias="CUCM_COMMAND_TIMEOUT")
    cucm_prompt_timeout: int = Field(default=15, alias="CUCM_PROMPT_TIMEOUT")
    cucm_ssh_known_hosts: Optional[str] = Field(default=None, alias="CUCM_SSH_KNOWN_HOSTS")
    cucm_ssh_host_key_fingerprint: Optional[str] = Field(default=None, alias="CUCM_SSH_HOST_KEY_FINGERPRINT")
    cucm_ssh_strict_host_key: bool = Field(default=True, alias="CUCM_SSH_STRICT_HOST_KEY")

    # AI / LLM Configuration
    llm_provider: str = Field(default="gateway", alias="LLM_PROVIDER")
    llm_model: str = Field(default="gemini-3.1-flash-lite", alias="LLM_MODEL")
    llm_timeout: int = Field(default=60, alias="LLM_TIMEOUT")
    llm_max_retries: int = Field(default=2, alias="LLM_MAX_RETRIES")
    ollama_base_url: str = Field(default="http://10.106.44.12:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="llama3", alias="OLLAMA_MODEL")
    gemini_api_key: Optional[SecretStr] = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-3.1-flash-lite", alias="GEMINI_MODEL")
    openrouter_api_key: Optional[SecretStr] = Field(default=None, alias="OPENROUTER_API_KEY")
    openrouter_models: str = Field(
        default="liquid/lfm-2.5-2.6b:free,google/gemma-4-26b-a4b-it:free,qwen/qwen3.8-27b:free",
        alias="OPENROUTER_MODELS",
    )
    openai_api_key: Optional[SecretStr] = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")

    @property
    def openrouter_models_list(self) -> List[str]:
        return [m.strip() for m in self.openrouter_models.split(",") if m.strip()]

    @staticmethod
    def _read_sibling_secret(key_name: str) -> str:
        """Reuse approved secret from sibling reference .env if not set locally (without copying/exposing)."""
        ref_env = PROJECT_ROOT.parent / "EnterpriseAgenticAIforVoiceopsAssistant" / ".env"
        if not ref_env.exists():
            return ""
        try:
            for line in ref_env.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                if k.strip() == key_name and v.strip():
                    return v.strip()
        except Exception:
            pass
        return ""

    @property
    def resolved_gemini_api_key(self) -> str:
        if self.gemini_api_key and self.gemini_api_key.get_secret_value().strip():
            return self.gemini_api_key.get_secret_value().strip()
        return self._read_sibling_secret("GEMINI_API_KEY")

    @property
    def resolved_openrouter_api_key(self) -> str:
        if self.openrouter_api_key and self.openrouter_api_key.get_secret_value().strip():
            return self.openrouter_api_key.get_secret_value().strip()
        return self._read_sibling_secret("OPENROUTER_API_KEY")

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def effective_verify_ssl(self) -> bool:
        """Enforce SSL verification in production regardless of dev override."""
        if self.is_production:
            return True
        return self.cucm_verify_ssl

    @property
    def effective_strict_host_key(self) -> bool:
        """Enforce strict SSH host key verification in production."""
        if self.is_production:
            return True
        return self.cucm_ssh_strict_host_key

    @staticmethod
    def _is_placeholder(secret: Optional[SecretStr]) -> bool:
        if secret is None:
            return True
        val = secret.get_secret_value().strip()
        return not val or val == "<SET_LOCALLY>"

    def has_axl_credentials(self) -> bool:
        return bool(self.cucm_host and self.cucm_username and not self._is_placeholder(self.cucm_password))

    def has_cli_credentials(self) -> bool:
        return bool(self.cucm_host and self.cucm_cli_username and not self._is_placeholder(self.cucm_cli_password))


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return cached AppSettings instance."""
    return AppSettings()


def load_yaml_config(filename: str) -> Dict[str, Any]:
    """Load a YAML file from the config/ directory."""
    path = CONFIG_DIR / filename
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def load_settings_yaml() -> Dict[str, Any]:
    return load_yaml_config("settings.yaml")


def load_devices_yaml() -> List[Dict[str, Any]]:
    data = load_yaml_config("devices.yaml")
    return data.get("devices", [])


def load_commands_yaml() -> List[Dict[str, Any]]:
    data = load_yaml_config("commands.yaml")
    return data.get("commands", [])
