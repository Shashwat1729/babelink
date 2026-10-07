"""LLM provider protocol + a deterministic MockProvider for tests/CI."""
from __future__ import annotations

import json
import re
from typing import Protocol


def parse_findings(text: str) -> list[dict]:
    import json
    import re

    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return [d for d in data if isinstance(d, dict) and "type" in d]
    except Exception:
        return []


class LLMProvider(Protocol):
    name: str

    def translate(self, source: str, glossary: dict[str, str] | None = None, context: str = "", max_tokens: int = 1024) -> str:
        ...

    def refine(self, source: str, draft: str, style: str = "", max_tokens: int = 1024) -> str:
        ...

    def review(self, source: str, translation: str) -> list[dict]:
        """AI QA: return structured findings [{'severity','type','message'}]."""
        ...


class MockProvider:
    """Deterministic stand-in: applies glossary substitutions and tags the text.

    No network, no GPU. Lets the full pipeline run in CI.
    """

    name = "mock"

    def translate(self, source: str, glossary: dict[str, str] | None = None, context: str = "", max_tokens: int = 1024) -> str:
        out = source
        for src, dst in (glossary or {}).items():
            out = out.replace(src, dst)
        return out

    def refine(self, source: str, draft: str, style: str = "", max_tokens: int = 1024) -> str:
        return draft

    def review(self, source: str, translation: str) -> list[dict]:
        return []
