"""Google Gemini REST API LLM client."""

import httpx

from backend.ai.llm_factory import BaseLLMClient


class GeminiLLMClient(BaseLLMClient):
    provider_name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash", timeout: int = 60):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def generate(self, system_prompt: str, user_prompt: str, json_mode: bool = True) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_prompt}]}],
        }
        if json_mode:
            payload["generationConfig"] = {"responseMimeType": "application/json"}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, params={"key": self.api_key}, json=payload)
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return ""
            parts = candidates[0].get("content", {}).get("parts", [])
            return parts[0].get("text", "") if parts else ""
