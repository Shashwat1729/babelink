"""Ollama provider."""
from __future__ import annotations

import httpx


class OllamaProvider:
    def __init__(self, model: str = "qwen2.5:7b", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.name = f"ollama:{model}"

    def translate(self, source: str, glossary: dict[str, str] | None = None, max_tokens: int = 1024) -> str:
        glossary_block = ""
        if glossary:
            lines = "\n".join(f"- {k} -> {v}" for k, v in glossary.items())
            glossary_block = f"\nCanonical terminology (use exactly these):\n{lines}\n"
        prompt = (
            "Translate the following fiction into the target language. Preserve meaning, "
            "names, terminology, and paragraph structure. Output only the translation.\n"
            f"{glossary_block}\nSource:\n{source}"
        )
        resp = httpx.post(
            f"{self.base_url}/api/generate",
            json={"model": self.model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2, "num_predict": max_tokens}},
            timeout=600,
        )
        resp.raise_for_status()
        return resp.json()["response"].strip()
