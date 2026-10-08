"""OpenAI-compatible endpoint provider (llama.cpp server, vLLM, LM Studio, hosted APIs)."""
from __future__ import annotations

import json

import httpx

from .base import ChatProvider, Delta


class OpenAICompatibleProvider(ChatProvider):
    def __init__(self, base_url: str = "http://localhost:8080/v1", model: str = "local", api_key: str = "",
                 extra_body: dict | None = None, timeout: float = 600):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.extra_body = extra_body or {}  # e.g. {"chat_template_kwargs": {"enable_thinking": False}} on llama.cpp/vLLM
        self.timeout = timeout
        self.name = f"openai-compatible:{model}"

    def _chat_once(self, system: str, user: str, temperature: float, max_tokens: int, on_delta: Delta | None = None) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = {"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": temperature, "max_tokens": max_tokens, **self.extra_body}
        if on_delta:
            return self._stream(body, headers, on_delta)
        resp = httpx.post(f"{self.base_url}/chat/completions", json=body, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        use = data.get("usage") or {}
        self.usage["prompt_tokens"] += use.get("prompt_tokens", 0)
        self.usage["completion_tokens"] += use.get("completion_tokens", 0)
        return data["choices"][0]["message"]["content"] or ""

    def _stream(self, body: dict, headers: dict, on_delta: Delta) -> str:
        """Server-sent events: `data: {json}` lines, `choices[0].delta.content` is the next piece, `data: [DONE]` ends."""
        text = ""
        payload_body = {**body, "stream": True, "stream_options": {"include_usage": True}}
        with httpx.stream("POST", f"{self.base_url}/chat/completions", json=payload_body, headers=headers, timeout=self.timeout) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                data = json.loads(payload)
                use = data.get("usage") or {}
                self.usage["prompt_tokens"] += use.get("prompt_tokens", 0)
                self.usage["completion_tokens"] += use.get("completion_tokens", 0)
                piece = ((data.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
                if piece:
                    text += piece
                    on_delta(text)
        return text


def installed(base_url: str = "http://localhost:8080/v1", api_key: str = "") -> list[dict]:
    """Models a server lists at GET /models. Raises when it is unreachable or does not implement it."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    r = httpx.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=5)
    r.raise_for_status()
    return [{"name": m["id"], "size_gb": 0, "params": "", "quant": ""} for m in sorted(r.json().get("data", []), key=lambda m: m["id"])]
