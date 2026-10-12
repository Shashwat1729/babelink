"""Hy-MT2 on Ollama (`ollama pull hf.co/tencent/Hy-MT2-1.8B-GGUF:Q4_K_M`).

Translation-only model: translate() uses the official Hy-MT2 instruction
format (see lorebridge.hymt) with the released sampling settings. The other
ChatProvider tasks (refine/review/suggest_terms/summarize) still work through
the generic prompts, but quality is poor compared to a general LLM — the
service layer routes those tasks to a helper model (see service.helper_for),
so this class is only the translation engine.

Transport note: the Ollama model `hymt2-1.8b` (built by Modelfile.hymt2 from
the official GGUF) carries the exact Hy-MT2 chat template, so translate() goes
through the normal /api/chat path with the official instruction format and the
released sampling settings. (Sending the template's control tokens as raw text
through /api/generate BPE-fragments them and produces degenerate output.)
"""
from __future__ import annotations

from typing import Optional

from .. import prompts
from ..hymt import DEFAULT_MODEL, is_mt_model, mt_prompt
from .base import ChatProvider, Delta, auto_max_tokens
from .ollama import OllamaProvider


class HymtOllamaProvider(OllamaProvider):
    """Ollama-backed Hy-MT2. Drop-in replacement for OllamaProvider for translate()."""

    translation_only = True
    prompt_features = frozenset({"glossary", "background", "tm_hints", "style"})  # trained with all four blocks

    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = "http://localhost:11434",
                 num_ctx: int = 8192, timeout: float = 900):
        super().__init__(model=model, base_url=base_url, num_ctx=num_ctx, timeout=timeout)
        self.name = f"hymt:{model}"

    def translate(self, source: str, glossary: dict[str, str] | None = None, context: str = "",
                  max_tokens: int | None = None, *, style: str = "", source_lang: str = "zh",
                  target_lang: str = "en", tm_hints=None, on_delta: Optional[Delta] = None) -> str:
        from ..hymt import SAMPLING

        mt = max_tokens or auto_max_tokens(source)
        # MT models take background, not chatty context headers: pass the story
        # context through as background info (official Structured-Data-2 block).
        # TM hints are harness-level memory, not terminology: fold them into the
        # background so the model sees them without an unfamiliar format.
        background = context
        if tm_hints:
            background = (background + "\n" if background else "") + "Similar lines translated earlier (reference only):\n" + \
                         "\n".join(f"{s} => {t}" for s, t in tm_hints)
        _system, user = mt_prompt(source, glossary=glossary, style=style,
                                  source_lang=source_lang, target_lang=target_lang, background=background)
        out = prompts.clean_output(self.chat("", user, temperature=SAMPLING["temperature"], max_tokens=mt, on_delta=on_delta))
        if prompts.looks_like_refusal(out, source):
            self.usage["refusals"] += 1
            _s, neutral = prompts.neutral_prompt(source, source_lang, target_lang)
            out = prompts.clean_output(self.chat("", neutral, temperature=0.3, max_tokens=mt, on_delta=on_delta))
            if prompts.looks_like_refusal(out, source):
                raise prompts.RefusalError(out[:200] or "empty response")
        return out


def is_mt_name(name: str) -> bool:
    return is_mt_model(name)
