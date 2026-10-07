"""LLM provider protocol + a deterministic MockProvider for tests/CI."""
from __future__ import annotations

import json
import re
from typing import Protocol


class LLMProvider(Protocol):
    name: str

    def translate(self, source: str, glossary: dict[str, str] | None = None, context: str = "", max_tokens: int = 1024) -> str:
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
