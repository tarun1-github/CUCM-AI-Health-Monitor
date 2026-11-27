"""Ollama local LLM client."""

import httpx

from backend.ai.llm_factory import BaseLLMClient


class OllamaLLMClient(BaseLLMClient):
    provider_name = "ollama"

    def __init__(self, base_url: str = "http://10.106.44.12:11434", model: str = "llama3", timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def generate(self, system_prompt: str, user_prompt: str, json_mode: bool = True) -> str:
        payload = {
            "model": self.model,
            "prompt": f"{system_prompt}\n\n{user_prompt}",
            "stream": False,
        }
        if json_mode:
            payload["format"] = "json"
        with httpx.Client(trust_env=False, timeout=self.timeout) as client:
            resp = client.post(f"{self.base_url}/api/generate", json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "")
