"""Interchangeable LLM provider factory."""

from abc import ABC, abstractmethod
from typing import Optional

from backend.utils.config_loader import get_settings


class BaseLLMClient(ABC):
    """Abstract interface for LLM providers."""

    provider_name: str = "base"

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Generate a text/JSON response from the LLM."""


def create_llm_client(provider: Optional[str] = None) -> Optional[BaseLLMClient]:
    """Instantiate configured LLM client, or return None if disabled/unconfigured."""
    settings = get_settings()
    selected = (provider or settings.llm_provider or "none").strip().lower()

    if selected in ("none", "", "disabled"):
        return None
    if selected in ("gateway", "auto"):
        from backend.ai.gateway import LLMGatewayClient
        return LLMGatewayClient()
    if selected == "ollama":
        from backend.ai.ollama_client import OllamaLLMClient
        return OllamaLLMClient(base_url=settings.ollama_base_url, model=settings.ollama_model, timeout=settings.llm_timeout)
    if selected == "gemini":
        api_key = settings.resolved_gemini_api_key
        if not api_key:
            return None
        from backend.ai.gemini_client import GeminiLLMClient
        return GeminiLLMClient(api_key=api_key, model=settings.gemini_model, timeout=settings.llm_timeout)
    if selected == "openrouter":
        api_key = settings.resolved_openrouter_api_key
        if not api_key:
            return None
        from backend.ai.openrouter_client import OpenRouterLLMClient
        return OpenRouterLLMClient(api_key=api_key, models=settings.openrouter_models_list, timeout=settings.llm_timeout)
    if selected == "openai":
        api_key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
        if not api_key:
            return None
        from backend.ai.openai_client import OpenAILLMClient
        return OpenAILLMClient(api_key=api_key, model=settings.openai_model, timeout=settings.llm_timeout)
    return None
