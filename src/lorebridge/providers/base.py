"""LLM provider layer: one prompt-building base (`ChatProvider`) + a recording MockProvider.

Concrete providers only implement `chat()`; prompts, refusal handling (spec §63), output
cleaning, usage counting and retries live here so every backend behaves the same.
"""
from __future__ import annotations

import time
from typing import Callable, Optional, Protocol

import httpx

from .. import prompts
from ..prompts import ProviderError, RefusalError  # noqa: F401  (re-exported)


def parse_findings(text: str) -> list[dict]:
    return [d for d in prompts.parse_json_array(text) if "type" in d]


Delta = Callable[[str], None]  # called with the text produced so far (not just the new piece)


def auto_max_tokens(source: str) -> int:
    """Output budget from source size: CJK expands ~2-3x into English tokens."""
    return max(256, min(3072, int(len(source) * 2.5) + 128))


class LLMProvider(Protocol):
    name: str

    def translate(self, source: str, glossary: dict[str, str] | None = None, context: str = "", max_tokens: int | None = None, **kw) -> str: ...
    def refine(self, source: str, draft: str, style: str = "", max_tokens: int | None = None, **kw) -> str: ...
    def review(self, source: str, translation: str) -> list[dict]: ...
    def suggest_terms(self, items: list[tuple[str, str]], source_lang: str = "zh", target_lang: str = "en") -> list[dict]: ...
    def summarize(self, text: str, target_lang: str = "en") -> str: ...


class ChatProvider:
    """Base for chat-style backends. Subclasses set `name` and implement `_chat_once`.

    `prompt_features` declares which harness controls the engine was trained to
    consume in-prompt. The pipeline always passes everything; each translate()
    sends only its declared subset. Anything undeclared is enforced OUTSIDE the
    prompt (guided retry, vote+normalize) — narrow MT models go pathological on
    unfamiliar machinery (E05: glossary preamble hung CAT-1.4B >15min). A
    declaration per ENGINE, never per sentence."""

    name = "chat"
    retries = 2
    prompt_features = frozenset({"glossary", "background", "tm_hints", "style"})

    def __init__(self) -> None:
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "refusals": 0}

    # -- backend hook -------------------------------------------------------------------------
    def _chat_once(self, system: str, user: str, temperature: float, max_tokens: int, on_delta: Optional[Delta] = None) -> str:
        """Return the reply. With `on_delta`, stream it and call `on_delta(text_so_far)` as it grows."""
        raise NotImplementedError

    def chat(self, system: str, user: str, *, temperature: float = 0.2, max_tokens: int = 1024, on_delta: Optional[Delta] = None) -> str:
        err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                out = self._chat_once(system, user, temperature, max_tokens, on_delta) if on_delta else self._chat_once(system, user, temperature, max_tokens)
                self.usage["calls"] += 1
                return out
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                err = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    break  # client error: retrying will not help
                time.sleep(1.5 * (attempt + 1))
        raise ProviderError(f"{self.name}: {err}") from err

    # -- tasks --------------------------------------------------------------------------------
    def translate(self, source: str, glossary: dict[str, str] | None = None, context: str = "", max_tokens: int | None = None,
                  *, style: str = "", source_lang: str = "zh", target_lang: str = "en", tm_hints=None, on_delta: Optional[Delta] = None) -> str:
        mt = max_tokens or auto_max_tokens(source)
        system, user = prompts.translation_prompt(source, glossary=glossary, context=context, style=style,
                                                  source_lang=source_lang, target_lang=target_lang, tm_hints=tm_hints)
        out = prompts.clean_output(self.chat(system, user, temperature=0.2, max_tokens=mt, on_delta=on_delta))
        if prompts.looks_like_refusal(out, source):
            self.usage["refusals"] += 1
            system, user = prompts.neutral_prompt(source, source_lang, target_lang)
            out = prompts.clean_output(self.chat(system, user, temperature=0.3, max_tokens=mt, on_delta=on_delta))
            if prompts.looks_like_refusal(out, source):
                raise RefusalError(out[:200] or "empty response")
        return out

    def refine(self, source: str, draft: str, style: str = "", max_tokens: int | None = None, *, glossary=None,
               source_lang: str = "zh", target_lang: str = "en") -> str:
        system, user = prompts.refinement_prompt(source, draft, style=style, glossary=glossary,
                                                 source_lang=source_lang, target_lang=target_lang)
        out = prompts.clean_output(self.chat(system, user, temperature=0.3, max_tokens=max_tokens or auto_max_tokens(source)))
        return draft if prompts.looks_like_refusal(out, draft) else out

    def review(self, source: str, translation: str) -> list[dict]:
        system, user = prompts.review_prompt(source, translation)
        return parse_findings(self.chat(system, user, temperature=0.0, max_tokens=512))

    def suggest_terms(self, items: list[tuple[str, str]], source_lang: str = "zh", target_lang: str = "en") -> list[dict]:
        if not items:
            return []
        system, user = prompts.terms_prompt(items, source_lang, target_lang)
        found = prompts.parse_json_array(self.chat(system, user, temperature=0.0, max_tokens=60 * len(items) + 128))
        return [d for d in found if d.get("term") and d.get("translation")]

    def summarize(self, text: str, target_lang: str = "en") -> str:
        system, user = prompts.summary_prompt(text[:6000], target_lang)
        return prompts.clean_output(self.chat(system, user, temperature=0.2, max_tokens=300))


class MockProvider:
    """Deterministic stand-in: applies glossary substitutions. No network, no GPU.

    Records every call in `calls` so tests can assert what context the pipeline supplied.
    `refuse` = source substrings that trigger a RefusalError.
    """

    name = "mock"

    def __init__(self, refuse: tuple[str, ...] = ()) -> None:
        self.calls: list[dict] = []
        self.refuse = refuse
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "refusals": 0}

    def translate(self, source: str, glossary: dict[str, str] | None = None, context: str = "", max_tokens: int | None = None,
                  *, style: str = "", source_lang: str = "zh", target_lang: str = "en", tm_hints=None, on_delta: Optional[Delta] = None) -> str:
        self.calls.append({"source": source, "glossary": dict(glossary or {}), "context": context, "style": style,
                           "tm_hints": list(tm_hints or [])})
        self.usage["calls"] += 1
        if any(r in source for r in self.refuse):
            raise RefusalError("mock refusal")
        out = source
        for src, dst in (glossary or {}).items():
            out = out.replace(src, dst)
        if on_delta:
            on_delta(out)
        return out

    def refine(self, source: str, draft: str, style: str = "", max_tokens: int | None = None, **kw) -> str:
        return draft

    def review(self, source: str, translation: str) -> list[dict]:
        return []

    def suggest_terms(self, items, source_lang: str = "zh", target_lang: str = "en") -> list[dict]:
        return []

    def summarize(self, text: str, target_lang: str = "en") -> str:
        return ""
