"""Deterministic Multi-LLM Gateway with Hierarchical Failover: Gemini -> OpenRouter -> Remote Ollama."""

import re
import time
from typing import Any, Dict, List, Optional

from backend.ai.llm_factory import BaseLLMClient
from backend.utils.config_loader import get_settings
from backend.utils.logger import get_logger

logger = get_logger("llm.gateway", category="ai")


def _sanitize(err: Any) -> str:
    text = str(err)
    text = re.sub(r"AIzaSy[A-Za-z0-9_-]{8,}", "[REDACTED_GEMINI_KEY]", text)
    text = re.sub(r"sk-or-v1-[A-Za-z0-9_-]{8,}", "[REDACTED_OPENROUTER_KEY]", text)
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[REDACTED_KEY]", text)
    text = re.sub(r"key=[A-Za-z0-9_-]{8,}", "key=[REDACTED]", text)
    return text


def _is_permanent_error(err_text: str) -> bool:
    s = err_text.lower()
    return any(
        k in s
        for k in (
            "401",
            "403",
            "404",
            "unauthorized",
            "invalid api key",
            "api key not valid",
            "not found",
            "missing",
        )
    )


class LLMGatewayClient(BaseLLMClient):
    """Hierarchical failover gateway: Gemini (Primary) -> OpenRouter (Secondary) -> Remote Ollama (Final)."""

    provider_name = "gateway"

    def __init__(
        self,
        gemini: Optional[BaseLLMClient] = None,
        openrouter: Optional[BaseLLMClient] = None,
        ollama: Optional[BaseLLMClient] = None,
        openrouter_models: Optional[List[str]] = None,
        max_retries: Optional[int] = None,
    ):
        s = get_settings()
        self.max_retries = max_retries if max_retries is not None else max(0, int(s.llm_max_retries))
        self.openrouter_models = openrouter_models or s.openrouter_models_list

        if gemini is not None:
            self.gemini: Optional[BaseLLMClient] = gemini
        elif s.resolved_gemini_api_key:
            from backend.ai.gemini_client import GeminiLLMClient
            self.gemini = GeminiLLMClient(
                api_key=s.resolved_gemini_api_key,
                model=s.gemini_model or s.llm_model,
                timeout=s.llm_timeout,
            )
        else:
            self.gemini = None

        if openrouter is not None:
            self.openrouter: Optional[BaseLLMClient] = openrouter
        elif s.resolved_openrouter_api_key:
            from backend.ai.openrouter_client import OpenRouterLLMClient
            self.openrouter = OpenRouterLLMClient(
                api_key=s.resolved_openrouter_api_key,
                models=self.openrouter_models,
                timeout=s.llm_timeout,
            )
        else:
            self.openrouter = None

        if ollama is not None:
            self.ollama: Optional[BaseLLMClient] = ollama
        elif s.ollama_base_url and s.ollama_model:
            from backend.ai.ollama_client import OllamaLLMClient
            self.ollama = OllamaLLMClient(
                base_url=s.ollama_base_url,
                model=s.ollama_model,
                timeout=s.llm_timeout,
            )
        else:
            self.ollama = None

        # Runtime provenance telemetry
        self.last_used_provider: Optional[str] = None
        self.last_used_model: Optional[str] = None
        self.last_latency_ms: float = 0.0
        self.fallback_used: bool = False
        self.original_failure: Optional[str] = None
        self.failover_history: List[Dict[str, Any]] = []

    @property
    def model(self) -> Optional[str]:
        return self.last_used_model or (getattr(self.gemini, "model", None) if self.gemini else None)

    def _call_with_retry(self, client: BaseLLMClient, system_prompt: str, user_prompt: str, json_mode: bool) -> str:
        attempts = 1 + self.max_retries
        last_exc: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                try:
                    return client.generate(system_prompt, user_prompt, json_mode=json_mode)
                except TypeError:
                    return client.generate(system_prompt, user_prompt)
            except Exception as exc:
                last_exc = exc
                if _is_permanent_error(_sanitize(exc)) or attempt >= attempts - 1:
                    raise
                time.sleep(0.3)
        raise last_exc or RuntimeError("LLM call failed")

    def generate(self, system_prompt: str, user_prompt: str, json_mode: bool = True) -> str:
        self.failover_history = []
        self.fallback_used = False
        self.original_failure = None
        errors: List[str] = []

        # 1. PRIMARY: Google Gemini
        if self.gemini is not None:
            t0 = time.perf_counter()
            model_name = getattr(self.gemini, "model", "gemini")
            try:
                res = self._call_with_retry(self.gemini, system_prompt, user_prompt, json_mode)
                if not res or not res.strip():
                    raise ValueError("Empty response from Gemini")
                lat = round((time.perf_counter() - t0) * 1000.0, 1)
                self.last_used_provider = "gemini"
                self.last_used_model = model_name
                self.last_latency_ms = lat
                self.failover_history.append({"provider": "gemini", "model": model_name, "status": "OK", "latency_ms": lat})
                return res
            except Exception as exc:
                lat = round((time.perf_counter() - t0) * 1000.0, 1)
                clean = _sanitize(exc)
                self.original_failure = f"Gemini ({model_name}): {clean}"
                errors.append(self.original_failure)
                self.failover_history.append({"provider": "gemini", "model": model_name, "status": "FAILED", "error": clean, "latency_ms": lat})
        else:
            self.original_failure = "Gemini not configured (missing GEMINI_API_KEY)"
            errors.append(self.original_failure)
            self.failover_history.append({"provider": "gemini", "status": "SKIPPED", "error": "Not configured"})

        # 2. SECONDARY: OpenRouter (model list iteration)
        if self.openrouter is not None:
            for model_name in self.openrouter_models:
                t0 = time.perf_counter()
                try:
                    if hasattr(self.openrouter, "set_model"):
                        self.openrouter.set_model(model_name)
                    res = self._call_with_retry(self.openrouter, system_prompt, user_prompt, json_mode)
                    if not res or not res.strip():
                        raise ValueError(f"Empty response from OpenRouter ({model_name})")
                    lat = round((time.perf_counter() - t0) * 1000.0, 1)
                    self.last_used_provider = "openrouter"
                    self.last_used_model = model_name
                    self.last_latency_ms = lat
                    self.fallback_used = True
                    self.failover_history.append({"provider": "openrouter", "model": model_name, "status": "OK", "latency_ms": lat})
                    return res
                except Exception as exc:
                    lat = round((time.perf_counter() - t0) * 1000.0, 1)
                    clean = _sanitize(exc)
                    errors.append(f"OpenRouter ({model_name}): {clean}")
                    self.failover_history.append({"provider": "openrouter", "model": model_name, "status": "FAILED", "error": clean, "latency_ms": lat})
        else:
            errors.append("OpenRouter not configured (missing OPENROUTER_API_KEY)")
            self.failover_history.append({"provider": "openrouter", "status": "SKIPPED", "error": "Not configured"})

        # 3. FINAL FALLBACK: Remote Ollama
        if self.ollama is not None:
            t0 = time.perf_counter()
            model_name = getattr(self.ollama, "model", "llama3")
            try:
                res = self._call_with_retry(self.ollama, system_prompt, user_prompt, json_mode)
                if not res or not res.strip():
                    raise ValueError(f"Empty response from Ollama ({model_name})")
                lat = round((time.perf_counter() - t0) * 1000.0, 1)
                self.last_used_provider = "ollama"
                self.last_used_model = model_name
                self.last_latency_ms = lat
                self.fallback_used = True
                self.failover_history.append({"provider": "ollama", "model": model_name, "status": "OK", "latency_ms": lat})
                return res
            except Exception as exc:
                lat = round((time.perf_counter() - t0) * 1000.0, 1)
                clean = _sanitize(exc)
                errors.append(f"Ollama ({model_name}): {clean}")
                self.failover_history.append({"provider": "ollama", "model": model_name, "status": "FAILED", "error": clean, "latency_ms": lat})
        else:
            errors.append("Ollama not configured")
            self.failover_history.append({"provider": "ollama", "status": "SKIPPED", "error": "Not configured"})

        self.last_used_provider = None
        self.last_used_model = None
        raise RuntimeError("All configured LLM providers failed: " + " | ".join(errors))
