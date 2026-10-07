"""OpenAI-compatible endpoint provider (works for local servers like llama.cpp, vLLM, LM Studio)."""
from __future__ import annotations

import httpx


class OpenAICompatibleProvider:
    def __init__(self, base_url: str = "http://localhost:8080/v1", model: str = "local", api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.name = f"openai-compatible:{model}"

    def translate(self, source: str, glossary: dict[str, str] | None = None, context: str = "", max_tokens: int = 1024) -> str:
        glossary_block = ""
        if glossary:
            lines = "\n".join(f"- {k} -> {v}" for k, v in glossary.items())
            glossary_block = f"\nCanonical terminology (use exactly these):\n{lines}\n"
        context_block = f"\nStory context:\n{context}\n" if context else ""
        prompt = (
            "Translate the following fiction into the target language. Preserve meaning, "
            "names, terminology, and paragraph structure. Output only the translation.\n"
            f"{glossary_block}{context_block}\nSource:\n{source}"
        )
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": max_tokens,
            },
            headers=headers,
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
