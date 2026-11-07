"""Ollama provider (/api/chat).

`think: false` matters: qwen3-family models otherwise spend 20-100 s "thinking" per call on CPU
and may return only reasoning text (see docs/REGRESSIONS.md).
"""
from __future__ import annotations

import json

import httpx

from .base import ChatProvider, Delta


class OllamaProvider(ChatProvider):
    def __init__(self, model: str = "qwen2.5:7b", base_url: str = "http://localhost:11434", num_ctx: int = 8192, timeout: float = 900, keep_alive: str = "30m"):
        super().__init__()
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx
        self.timeout = timeout
        self.keep_alive = keep_alive
        self.name = f"ollama:{model}"

    def warm_up(self, prompt: str = "hi", max_tokens: int = 1, timeout: float = 120) -> float:
        """Load this model into Ollama memory and start the keep_alive clock.

        Uses a 1-token generation so warmup cost is minimal. Returns seconds
        spent. Calling this before a translation run removes model-load
        latency from the first segment (see fallback.warmup + bench).
        """
        import time

        t0 = time.time()
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"num_predict": max_tokens, "num_ctx": self.num_ctx},
        }
        resp = httpx.post(f"{self.base_url}/api/chat", json=body, timeout=timeout)
        resp.raise_for_status()
        return time.time() - t0

    def _chat_once(self, system: str, user: str, temperature: float, max_tokens: int, on_delta: Delta | None = None) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": on_delta is not None,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": self.num_ctx},
        }
        if on_delta:
            return self._stream(body, on_delta)
        resp = httpx.post(f"{self.base_url}/api/chat", json=body, timeout=self.timeout)
        if resp.status_code == 400 and "think" in resp.text.lower():  # older server / model without think support
            body.pop("think")
            resp = httpx.post(f"{self.base_url}/api/chat", json=body, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        self.usage["prompt_tokens"] += data.get("prompt_eval_count", 0)
        self.usage["completion_tokens"] += data.get("eval_count", 0)
        return data["message"]["content"]

    def _stream(self, body: dict, on_delta: Delta) -> str:
        """NDJSON: one JSON object per line, `message.content` is the next piece, the last line carries the token counts."""
        for _ in range(2):
            with httpx.stream("POST", f"{self.base_url}/api/chat", json=body, timeout=self.timeout) as resp:
                if resp.status_code == 400 and "think" in body and "think" in resp.read().decode("utf-8", "replace").lower():
                    body.pop("think")  # older server / model without think support
                    continue
                resp.raise_for_status()
                text = ""
                for line in resp.iter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise httpx.TransportError(str(data["error"]))
                    piece = (data.get("message") or {}).get("content") or ""
                    if piece:
                        text += piece
                        on_delta(text)
                    if data.get("done"):
                        self.usage["prompt_tokens"] += data.get("prompt_eval_count", 0)
                        self.usage["completion_tokens"] += data.get("eval_count", 0)
                return text
        raise httpx.TransportError("ollama: think fallback failed")


def installed(base_url: str = "http://localhost:11434") -> list[dict]:
    """Models Ollama has pulled, with size and quantization. Raises when the server is unreachable."""
    r = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=3)
    r.raise_for_status()
    return [{"name": m["name"], "size_gb": round(m.get("size", 0) / 2**30, 1),
             "params": (m.get("details") or {}).get("parameter_size", ""), "quant": (m.get("details") or {}).get("quantization_level", "")}
            for m in sorted(r.json().get("models", []), key=lambda m: m["name"])]


def list_models(base_url: str = "http://localhost:11434") -> list[str]:
    """Installed model names, or [] when the server is not running."""
    try:
        return [m["name"] for m in installed(base_url)]
    except Exception:
        return []


def warm_model(model: str, base_url: str = "http://localhost:11434", keep_alive: str = "30m", num_ctx: int = 4096, timeout: float = 120) -> float:
    """Load a raw Ollama model by name (no provider object needed). Returns seconds.

    Keeps engine+helper resident across alternation when called for both right
    before a run. Pair with OllamaProvider.warm_up for provider-owned warmup.
    """
    import time

    t0 = time.time()
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
        "keep_alive": keep_alive,
        "options": {"num_predict": 1, "num_ctx": num_ctx},
    }
    resp = httpx.post(f"{base_url.rstrip('/')}/api/chat", json=body, timeout=timeout)
    resp.raise_for_status()
    return time.time() - t0


def warm_provider(provider, timeout: float = 120) -> float | None:
    """Warm any provider: prefer its own warm_up(), else a 1-token chat.

    Returns seconds spent, or None when the provider cannot be warmed without
    a real task call (e.g. MockProvider needs no warmup).
    """
    import time

    fn = getattr(provider, "warm_up", None)
    if callable(fn):
        t0 = time.time()
        fn(timeout=timeout)
        return time.time() - t0
    chat = getattr(provider, "chat", None)
    if callable(chat):
        t0 = time.time()
        chat("", "hi", max_tokens=1)
        return time.time() - t0
    return None
