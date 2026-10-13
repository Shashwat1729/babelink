"""Google Gemini provider via the Generative Language API."""
from __future__ import annotations

import os
from typing import Optional

import httpx

from .base import ChatProvider, Delta


class GeminiProvider(ChatProvider):
    name = "gemini"

    def __init__(self, model: str = "gemini-2.0-flash", api_key: str = "", base_url: str = "https://generativelanguage.googleapis.com/v1beta"):
        super().__init__()
        self.model = model
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.name = f"gemini:{model}"

    def _chat_once(self, system: str, user: str, temperature: float, max_tokens: int, on_delta: Optional[Delta] = None) -> str:
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not set")
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        resp = httpx.post(
            f"{self.base_url}/models/{self.model}:generateContent",
            params={"key": self.api_key},
            json=payload,
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
        if on_delta:
            on_delta(text)
        return text
