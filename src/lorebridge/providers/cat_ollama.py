"""CyberAgent CAT-Translate on Ollama (GGUF via mradermacher, MIT licensed).

Translation-only ja<->en specialist (0.8/1.4/3.3/7B; we ship 1.4B Q4_K_M,
~930MB). translate() uses the official CAT instruction format inside the
model's own sarashina chat template (embedded in the GGUF, applied via
/api/chat — same transport lesson as Hy-MT2: never raw-generate control
tokens). Like Hy-MT2, helper tasks route to a general LLM (service.helper_for
keys off `translation_only`), so this class is only the translation engine.
"""
from __future__ import annotations

from typing import Optional

from .. import prompts
from .base import ChatProvider, Delta, auto_max_tokens
from .ollama import OllamaProvider

DEFAULT_MODEL = "cat-translate-1.4b"
_LANG = {"ja": "Japanese", "en": "English", "zh": "Chinese", "ko": "Korean"}


def cat_prompt(source: str, *, source_lang: str = "ja", target_lang: str = "en",
               glossary: dict[str, str] | None = None) -> str:
    """Official CAT instruction format. Glossary (untrained for CAT) rides as
    plain must-use lines above the source — applied best-effort."""
    src, tgt = _LANG.get(source_lang, source_lang), _LANG.get(target_lang, target_lang)
    head = f"Translate the following {src} text into {tgt}."
    if glossary:
        terms = "\n".join(f"{k} must be translated as {v}" for k, v in glossary.items())
        return f"{head}\nUse these translations:\n{terms}\n\n{source}"
    return f"{head}\n\n{source}"


class CatOllamaProvider(OllamaProvider):
    """Ollama-backed CAT-Translate. Drop-in engine alternative to Hy-MT2 for ja<->en."""

    translation_only = True
    # Narrow MT model: trained ONLY on the bare instruction (E05 — glossary/
    # background preambles send it pathological: >15min hang on one paragraph).
    # Glossary/context/TM/style are enforced OUTSIDE the prompt instead:
    # guided retry + vote + normalize_renderings. No per-sentence exceptions.
    prompt_features = frozenset()

    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = "http://localhost:11434",
                 num_ctx: int = 8192, timeout: float = 900):
        super().__init__(model=model, base_url=base_url, num_ctx=num_ctx, timeout=timeout)
        self.name = f"cat:{model}"

    def translate(self, source: str, glossary: dict[str, str] | None = None, context: str = "",
                  max_tokens: int | None = None, *, style: str = "", source_lang: str = "ja",
                  target_lang: str = "en", tm_hints=None, on_delta: Optional[Delta] = None) -> str:
        mt = max_tokens or auto_max_tokens(source)
        user = cat_prompt(source, source_lang=source_lang, target_lang=target_lang)  # bare: see prompt_features
        out = prompts.clean_output(self.chat("", user, temperature=0.2, max_tokens=mt, on_delta=on_delta))
        if prompts.looks_like_refusal(out, source):
            self.usage["refusals"] += 1
            _s, neutral = prompts.neutral_prompt(source, source_lang, target_lang)
            out = prompts.clean_output(self.chat("", neutral, temperature=0.3, max_tokens=mt, on_delta=on_delta))
            if prompts.looks_like_refusal(out, source):
                raise prompts.RefusalError(out[:200] or "empty response")
        return out
