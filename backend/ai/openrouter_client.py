"""OpenRouter Unified AI API client."""

from typing import List, Optional

import httpx

from backend.ai.llm_factory import BaseLLMClient


class OpenRouterLLMClient(BaseLLMClient):
    provider_name = "openrouter"

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        models: Optional[List[str]] = None,
        timeout: int = 60,
    ):
        self.api_key = api_key.strip()
        self.models = models or ["liquid/lfm-2.5-2.6b:free"]
        self.model = model or self.models[0]
        self.timeout = timeout
        self.base_url = "https://openrouter.ai/api/v1/chat/completions"

    def set_model(self, model_name: str) -> None:
        self.model = model_name

    def generate(self, system_prompt: str, user_prompt: str, json_mode: bool = True) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/tarun1-github/EnterpriseAgenticAIforVoiceopsAssistant",
            "X-Title": "CUCM AI Health Monitor",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(self.base_url, headers=headers, json=payload)
            if resp.status_code == 429:
                raise RuntimeError(f"OpenRouter rate limit or quota exceeded (429) for model '{self.model}'.")
            if resp.status_code == 401:
                raise RuntimeError("OpenRouter authentication failed (401). Invalid API key.")
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            return choices[0]["message"]["content"] if choices else ""
