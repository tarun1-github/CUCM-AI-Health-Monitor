"""LLM Provider Manager: gateway/provider health checks, individual provider tests, and safe error categorization."""

import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from backend.ai.llm_factory import BaseLLMClient, create_llm_client
from backend.utils.config_loader import get_settings


def sanitize_llm_error(err: Any) -> str:
    """Strip any potential API keys or tokens from error messages."""
    text = str(err)
    text = re.sub(r"AIzaSy[A-Za-z0-9_-]{8,}", "[REDACTED_GEMINI_KEY]", text)
    text = re.sub(r"sk-or-v1-[A-Za-z0-9_-]{8,}", "[REDACTED_OPENROUTER_KEY]", text)
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[REDACTED_KEY]", text)
    text = re.sub(r"key=[A-Za-z0-9_-]{8,}", "key=[REDACTED]", text)
    return text


def categorize_llm_error(err: Any) -> str:
    """Map raw exceptions/HTTP errors to safe, actionable categories."""
    s = sanitize_llm_error(err).lower()
    if "10061" in s or "connection refused" in s or "connecterror" in s or "connecttimeout" in s:
        return "Connection refused / Unreachable"
    if "401" in s or "403" in s or "unauthorized" in s or "api key" in s or "authentication" in s:
        return "Authentication failure / Invalid API key"
    if "404" in s or ("model" in s and "not found" in s):
        return "Model not found"
    if "timeout" in s or "timed out" in s:
        return "Timeout"
    if "429" in s or "rate limit" in s:
        return "Rate limit"
    if "quota" in s or "insufficient_quota" in s:
        return "Quota exceeded"
    if "json" in s or "invalid response" in s:
        return "Invalid response"
    return "Provider error"


class LLMProviderManager:
    """Tracks live LLM gateway/provider configuration, connection health, and inference telemetry."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        get_settings.cache_clear()
        s = get_settings()
        self.configured_provider: str = (s.llm_provider or "gateway").strip().lower()
        self.model_override: Optional[str] = None
        self.fallback_enabled: bool = True
        self.provider_available: bool = False
        self.last_successful_call: Optional[str] = None
        self.last_used_provider: Optional[str] = None
        self.last_used_model: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_error_category: Optional[str] = None
        self.last_connection_test: Optional[Dict[str, Any]] = None
        self.last_inference_test: Optional[Dict[str, Any]] = None
        self.last_latency_ms: Optional[float] = None

    def get_active_model(self) -> Optional[str]:
        s = get_settings()
        if self.model_override:
            return self.model_override
        if self.configured_provider in ("gateway", "auto"):
            return f"{s.gemini_model} -> OpenRouter -> {s.ollama_model}"
        if self.configured_provider == "ollama":
            return s.ollama_model
        if self.configured_provider == "gemini":
            return s.gemini_model
        if self.configured_provider == "openrouter":
            return ", ".join(s.openrouter_models_list)
        if self.configured_provider == "openai":
            return s.openai_model
        return None

    def get_endpoint(self) -> Optional[str]:
        s = get_settings()
        if self.configured_provider in ("gateway", "auto"):
            return f"Gemini -> OpenRouter -> {s.ollama_base_url}"
        if self.configured_provider == "ollama":
            return s.ollama_base_url
        if self.configured_provider == "gemini":
            return "https://generativelanguage.googleapis.com"
        if self.configured_provider == "openrouter":
            return "https://openrouter.ai/api/v1"
        if self.configured_provider == "openai":
            return "https://api.openai.com"
        return None

    def get_auth_status(self) -> str:
        s = get_settings()
        p = self.configured_provider
        if p in ("none", "", "disabled"):
            return "Disabled (LLM_PROVIDER=none)"
        if p in ("gateway", "auto"):
            g = "OK" if s.resolved_gemini_api_key else "Missing"
            o = "OK" if s.resolved_openrouter_api_key else "Missing"
            return f"Gemini={g} | OpenRouter={o} | Ollama={s.ollama_base_url}"
        if p == "ollama":
            return f"Remote endpoint {s.ollama_base_url} (no API key)"
        if p == "gemini":
            return "API key configured" if s.resolved_gemini_api_key else "Missing GEMINI_API_KEY"
        if p == "openrouter":
            return "API key configured" if s.resolved_openrouter_api_key else "Missing OPENROUTER_API_KEY"
        if p == "openai":
            has_key = bool(s.openai_api_key and s.openai_api_key.get_secret_value().strip())
            return "API key configured" if has_key else "Missing OPENAI_API_KEY"
        return "Unknown provider"

    def get_client(self) -> Optional[BaseLLMClient]:
        s = get_settings()
        p = self.configured_provider
        if p in ("none", "", "disabled"):
            return None
        if p in ("gateway", "auto"):
            from backend.ai.gateway import LLMGatewayClient
            return LLMGatewayClient()
        if p == "ollama":
            from backend.ai.ollama_client import OllamaLLMClient
            return OllamaLLMClient(base_url=s.ollama_base_url, model=self.model_override or s.ollama_model, timeout=s.llm_timeout)
        if p == "gemini":
            key = s.resolved_gemini_api_key
            if not key:
                return None
            from backend.ai.gemini_client import GeminiLLMClient
            return GeminiLLMClient(api_key=key, model=self.model_override or s.gemini_model, timeout=s.llm_timeout)
        if p == "openrouter":
            key = s.resolved_openrouter_api_key
            if not key:
                return None
            from backend.ai.openrouter_client import OpenRouterLLMClient
            models = [self.model_override] if self.model_override else s.openrouter_models_list
            return OpenRouterLLMClient(api_key=key, models=models, timeout=s.llm_timeout)
        if p == "openai":
            key = s.openai_api_key.get_secret_value().strip() if s.openai_api_key else ""
            if not key:
                return None
            from backend.ai.openai_client import OpenAILLMClient
            return OpenAILLMClient(api_key=key, model=self.model_override or s.openai_model, timeout=s.llm_timeout)
        return create_llm_client(p)

    def configure(self, provider: str, model: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            self.configured_provider = (provider or "none").strip().lower()
            self.model_override = model.strip() if model and model.strip() else None
            self.provider_available = False
            self.last_error = None
            self.last_error_category = None
            self.last_connection_test = None
            self.last_inference_test = None
            return self.get_status()

    def test_single_provider_connection(self, provider: str, model: Optional[str] = None) -> Dict[str, Any]:
        s = get_settings()
        p = provider.strip().lower()
        t0 = time.perf_counter()
        now = datetime.now(timezone.utc).isoformat()

        if p in ("none", "", "disabled"):
            return {"connected": False, "provider": "none", "model": None, "latency_ms": 0.0, "status": "Disabled", "error": None, "timestamp": now}

        if p in ("gateway", "auto"):
            sub = {
                "gemini": self.test_single_provider_connection("gemini"),
                "openrouter": self.test_single_provider_connection("openrouter"),
                "ollama": self.test_single_provider_connection("ollama"),
            }
            any_ok = any(v["connected"] for v in sub.values())
            return {
                "connected": any_ok,
                "provider": "gateway",
                "model": self.get_active_model(),
                "latency_ms": round(sum(v["latency_ms"] for v in sub.values()), 1),
                "status": "Gateway Partially/Fully Reachable" if any_ok else "All Gateway Providers Unreachable",
                "error": None if any_ok else "; ".join(f"{k}: {v.get('error')}" for k, v in sub.items()),
                "providers": sub,
                "timestamp": now,
            }

        try:
            if p == "ollama":
                mdl = model or s.ollama_model
                base = s.ollama_base_url.rstrip("/")
                with httpx.Client(trust_env=False, timeout=5) as c:
                    r = c.get(f"{base}/api/tags")
                    r.raise_for_status()
                    models = [m.get("name", "") for m in r.json().get("models", [])]
                model_found = any(mdl == m or m.startswith(f"{mdl}:") for m in models)
                lat = round((time.perf_counter() - t0) * 1000.0, 1)
                return {
                    "connected": True,
                    "model_available": model_found,
                    "available_models": models,
                    "provider": "ollama",
                    "endpoint": base,
                    "model": mdl,
                    "latency_ms": lat,
                    "status": "Connected" if model_found else f"Connected (model '{mdl}' not in tags; installed: {models})",
                    "error": None if model_found else f"Model '{mdl}' not found on {base}",
                    "timestamp": now,
                }

            if p == "gemini":
                mdl = model or s.gemini_model
                key = s.resolved_gemini_api_key
                if not key:
                    raise RuntimeError("Missing GEMINI_API_KEY")
                with httpx.Client(timeout=8) as c:
                    r = c.get(f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}", params={"key": key})
                    r.raise_for_status()
                lat = round((time.perf_counter() - t0) * 1000.0, 1)
                return {"connected": True, "model_available": True, "provider": "gemini", "model": mdl, "latency_ms": lat, "status": "Connected", "error": None, "timestamp": now}

            if p == "openrouter":
                key = s.resolved_openrouter_api_key
                if not key:
                    raise RuntimeError("Missing OPENROUTER_API_KEY")
                with httpx.Client(timeout=8) as c:
                    r = c.get("https://openrouter.ai/api/v1/auth/key", headers={"Authorization": f"Bearer {key}"})
                    r.raise_for_status()
                lat = round((time.perf_counter() - t0) * 1000.0, 1)
                return {"connected": True, "model_available": True, "provider": "openrouter", "model": ", ".join(s.openrouter_models_list), "latency_ms": lat, "status": "Connected", "error": None, "timestamp": now}

            if p == "openai":
                mdl = model or s.openai_model
                key = s.openai_api_key.get_secret_value().strip() if s.openai_api_key else ""
                if not key:
                    raise RuntimeError("Missing OPENAI_API_KEY")
                with httpx.Client(timeout=8) as c:
                    r = c.get(f"https://api.openai.com/v1/models/{mdl}", headers={"Authorization": f"Bearer {key}"})
                    r.raise_for_status()
                lat = round((time.perf_counter() - t0) * 1000.0, 1)
                return {"connected": True, "model_available": True, "provider": "openai", "model": mdl, "latency_ms": lat, "status": "Connected", "error": None, "timestamp": now}

            raise RuntimeError(f"Unsupported provider '{p}'")
        except Exception as exc:
            lat = round((time.perf_counter() - t0) * 1000.0, 1)
            cat = categorize_llm_error(exc)
            clean = sanitize_llm_error(exc)
            return {"connected": False, "model_available": False, "provider": p, "model": model, "latency_ms": lat, "status": cat, "error": f"{cat}: {clean}", "timestamp": now}

    def test_connection(self) -> Dict[str, Any]:
        with self._lock:
            res = self.test_single_provider_connection(self.configured_provider, self.model_override)
            self.provider_available = bool(res.get("connected") and res.get("model_available", True))
            self.last_connection_test = res
            if not self.provider_available and res.get("error"):
                self.last_error = res["error"]
                self.last_error_category = res.get("status")
            elif self.provider_available:
                self.last_error = None
                self.last_error_category = None
            return res

    def test_single_provider_inference(self, provider: str, model: Optional[str] = None) -> Dict[str, Any]:
        s = get_settings()
        p = provider.strip().lower()
        now = datetime.now(timezone.utc).isoformat()
        t0 = time.perf_counter()
        try:
            client: Optional[BaseLLMClient] = None
            if p in ("gateway", "auto"):
                from backend.ai.gateway import LLMGatewayClient
                client = LLMGatewayClient()
            elif p == "gemini" and s.resolved_gemini_api_key:
                from backend.ai.gemini_client import GeminiLLMClient
                client = GeminiLLMClient(api_key=s.resolved_gemini_api_key, model=model or s.gemini_model, timeout=s.llm_timeout)
            elif p == "openrouter" and s.resolved_openrouter_api_key:
                from backend.ai.openrouter_client import OpenRouterLLMClient
                models = [model] if model else s.openrouter_models_list
                client = OpenRouterLLMClient(api_key=s.resolved_openrouter_api_key, models=models, timeout=s.llm_timeout)
            elif p == "ollama":
                from backend.ai.ollama_client import OllamaLLMClient
                client = OllamaLLMClient(base_url=s.ollama_base_url, model=model or s.ollama_model, timeout=s.llm_timeout)
            elif p == "openai" and s.openai_api_key and s.openai_api_key.get_secret_value().strip():
                from backend.ai.openai_client import OpenAILLMClient
                client = OpenAILLMClient(api_key=s.openai_api_key.get_secret_value().strip(), model=model or s.openai_model, timeout=s.llm_timeout)

            if client is None:
                return {"success": False, "provider": p, "model": model, "latency_ms": 0.0, "error": "Provider disabled or credentials missing.", "error_category": "Not configured", "timestamp": now}

            out = client.generate("Reply with valid JSON.", 'Return {"status": "ok"}')
            lat = round((time.perf_counter() - t0) * 1000.0, 1)
            if not out or not out.strip():
                raise ValueError("Empty response from LLM")

            actual_provider = getattr(client, "last_used_provider", None) or p
            actual_model = getattr(client, "last_used_model", None) or getattr(client, "model", model)
            return {
                "success": True,
                "provider": actual_provider,
                "model": actual_model,
                "fallback_used": getattr(client, "fallback_used", False),
                "failover_history": getattr(client, "failover_history", []),
                "latency_ms": lat,
                "sample_output": out.strip()[:160],
                "error": None,
                "timestamp": now,
            }
        except Exception as exc:
            lat = round((time.perf_counter() - t0) * 1000.0, 1)
            cat = categorize_llm_error(exc)
            clean = sanitize_llm_error(exc)
            return {"success": False, "provider": p, "model": model, "latency_ms": lat, "error": f"{cat}: {clean}", "error_category": cat, "timestamp": now}

    def test_inference(self) -> Dict[str, Any]:
        with self._lock:
            res = self.test_single_provider_inference(self.configured_provider, self.model_override)
            self.last_inference_test = res
            self.last_latency_ms = res.get("latency_ms")
            if res.get("success"):
                self.provider_available = True
                self.last_successful_call = res.get("timestamp")
                self.last_used_provider = res.get("provider")
                self.last_used_model = res.get("model")
                self.last_error = None
                self.last_error_category = None
            else:
                self.last_error = res.get("error")
                self.last_error_category = res.get("error_category")
            return res

    def record_call_result(
        self,
        success: bool,
        latency_ms: float,
        error: Optional[str] = None,
        actual_provider: Optional[str] = None,
        actual_model: Optional[str] = None,
    ) -> None:
        with self._lock:
            self.last_latency_ms = latency_ms
            if success:
                self.provider_available = True
                self.last_successful_call = datetime.now(timezone.utc).isoformat()
                self.last_used_provider = actual_provider
                self.last_used_model = actual_model
                self.last_error = None
                self.last_error_category = None
            elif error:
                self.last_error_category = categorize_llm_error(error)
                self.last_error = f"{self.last_error_category}: {sanitize_llm_error(error)}"

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            s = get_settings()
            return {
                "configured_provider": self.configured_provider,
                "configured_model": self.get_active_model(),
                "gateway_chain": ["gemini", "openrouter", "ollama"],
                "primary": {"provider": "gemini", "model": s.gemini_model, "configured": bool(s.resolved_gemini_api_key)},
                "secondary": {"provider": "openrouter", "models": s.openrouter_models_list, "configured": bool(s.resolved_openrouter_api_key)},
                "final_fallback": {"provider": "ollama", "model": s.ollama_model, "endpoint": s.ollama_base_url},
                "endpoint": self.get_endpoint(),
                "auth_status": self.get_auth_status(),
                "provider_available": self.provider_available,
                "fallback_enabled": self.fallback_enabled,
                "last_successful_call": self.last_successful_call,
                "last_used_provider": self.last_used_provider,
                "last_used_model": self.last_used_model,
                "last_error": self.last_error,
                "last_error_category": self.last_error_category,
                "last_latency_ms": self.last_latency_ms,
                "last_connection_test": self.last_connection_test,
                "last_inference_test": self.last_inference_test,
            }


_MANAGER: Optional[LLMProviderManager] = None


def get_llm_provider_manager() -> LLMProviderManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = LLMProviderManager()
    return _MANAGER
