"""OpenAI-compatible REST API LLM client."""

import httpx

from backend.ai.llm_factory import BaseLLMClient


class OpenAILLMClient(BaseLLMClient):
    provider_name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", timeout: int = 60):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def generate(self, system_prompt: str, user_prompt: str, json_mode: bool = True) -> str:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            return choices[0]["message"]["content"] if choices else ""
