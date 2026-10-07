"""OpenAI-compatible endpoint provider (works for local servers like llama.cpp, vLLM, LM Studio)."""
from __future__ import annotations

import httpx

from .base import parse_findings


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

    def refine(self, source: str, draft: str, style: str = "", max_tokens: int = 1024) -> str:
        prompt = (
            f"Improve the literary quality of this translation (style: {style or 'faithful-natural'}). "
            "Do NOT change names, terminology, numbers, facts, or add/remove content. Output only the refined translation.\n\n"
            f"Source:\n{source}\n\nDraft:\n{draft}"
        )
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            json={"model": self.model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3, "max_tokens": max_tokens},
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

    def review(self, source: str, translation: str) -> list[dict]:
        prompt = (
            "You are a translation QA checker. Compare source and translation. Return a JSON array of findings, "
            "each {\"severity\": \"warning|error\", \"type\": \"...\", \"message\": \"...\"}. If none, return [].\n\n"
            f"Source:\n{source}\n\nTranslation:\n{translation}"
        )
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            json={"model": self.model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.0, "max_tokens": 512},
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            timeout=300,
        )
        resp.raise_for_status()
        return parse_findings(resp.json()["choices"][0]["message"]["content"])
