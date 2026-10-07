"""Ollama provider."""
from __future__ import annotations

import httpx

from .base import parse_findings


class OllamaProvider:
    def __init__(self, model: str = "qwen2.5:7b", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.name = f"ollama:{model}"

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
        resp = httpx.post(
            f"{self.base_url}/api/generate",
            json={"model": self.model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2, "num_predict": max_tokens}},
            timeout=600,
        )
        resp.raise_for_status()
        return resp.json()["response"].strip()

    def refine(self, source: str, draft: str, style: str = "", max_tokens: int = 1024) -> str:
        prompt = (
            f"Improve the literary quality of this translation (style: {style or 'faithful-natural'}). "
            "Do NOT change names, terminology, numbers, facts, or add/remove content. Output only the refined translation.\n\n"
            f"Source:\n{source}\n\nDraft:\n{draft}"
        )
        resp = httpx.post(
            f"{self.base_url}/api/generate",
            json={"model": self.model, "prompt": prompt, "stream": False, "options": {"temperature": 0.3, "num_predict": max_tokens}},
            timeout=600,
        )
        resp.raise_for_status()
        return resp.json()["response"].strip()

    def review(self, source: str, translation: str) -> list[dict]:
        prompt = (
            "You are a translation QA checker. Compare source and translation. Return a JSON array of findings, "
            "each {\"severity\": \"warning|error\", \"type\": \"...\", \"message\": \"...\"}. If none, return [].\n\n"
            f"Source:\n{source}\n\nTranslation:\n{translation}"
        )
        resp = httpx.post(
            f"{self.base_url}/api/generate",
            json={"model": self.model, "prompt": prompt, "stream": False, "options": {"temperature": 0.0, "num_predict": 512}},
            timeout=600,
        )
        resp.raise_for_status()
        return parse_findings(resp.json()["response"])
