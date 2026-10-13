"""Hy-MT2 via 🤗 transformers (direct safetensors, no Ollama in the loop).

Use when the Ollama GGUF runner misbehaves for this architecture: this path
uses the model's own tokenizer + chat template exactly as released.
Weights live under HF_HOME (point it at D:\\StoryTranslation_data\\hf).
CPU bf16 on a 1.8B model is slow (~a few tok/s) but exact — fine for evals.
"""
from __future__ import annotations

from typing import Optional

import babelink.hymt as _hymt  # noqa: F401  (keeps prompt format in one place)
from .. import prompts
from ..hymt import SAMPLING, is_mt_model, mt_prompt
from .base import ChatProvider, Delta, auto_max_tokens

_REPO = "tencent/Hy-MT2-1.8B"


class HymtLocalProvider(ChatProvider):
    """transformers-backed Hy-MT2. Loads the model lazily on first translate()."""

    translation_only = True

    def __init__(self, model: str = _REPO, max_new_tokens_cap: int = 4096):
        super().__init__()
        self.model = model
        self._cap = max_new_tokens_cap
        self._tok = None
        self._mdl = None
        self.name = f"hymt-local:{model}"

    def _ensure(self):
        if self._mdl is not None:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._tok = AutoTokenizer.from_pretrained(self.model, trust_remote_code=True)
        self._mdl = AutoModelForCausalLM.from_pretrained(
            self.model, dtype=torch.bfloat16, device_map="auto", trust_remote_code=True)
        self._mdl.eval()

    def chat(self, system: str, user: str, temperature: float = 0.2, max_tokens: int = 300,
             on_delta: Delta | None = None) -> str:
        self._ensure()
        import torch

        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
        inputs = self._tok.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt").to(self._mdl.device)
        with torch.no_grad():
            out = self._mdl.generate(
                **inputs, max_new_tokens=min(max_tokens, self._cap),
                temperature=max(temperature, 0.05), top_p=SAMPLING["top_p"], top_k=SAMPLING["top_k"],
                repetition_penalty=SAMPLING["repetition_penalty"], do_sample=temperature > 0,
                eos_token_id=self._tok.eos_token_id, pad_token_id=self._tok.eos_token_id)
        text = self._tok.decode(out[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
        if on_delta:
            on_delta(text)
        return text

    def translate(self, source: str, glossary: dict[str, str] | None = None, context: str = "",
                  max_tokens: int | None = None, *, style: str = "", source_lang: str = "zh",
                  target_lang: str = "en", tm_hints=None, on_delta: Optional[Delta] = None) -> str:
        mt = max_tokens or auto_max_tokens(source)
        background = context
        if tm_hints:
            background = (background + "\n" if background else "") + "Similar lines translated earlier (reference only):\n" + \
                         "\n".join(f"{s} => {t}" for s, t in tm_hints)
        _system, user = mt_prompt(source, glossary=glossary, style=style,
                                  source_lang=source_lang, target_lang=target_lang, background=background)
        out = self.chat("", user, temperature=SAMPLING["temperature"], max_tokens=mt, on_delta=on_delta)
        out = prompts.clean_output(out)
        if prompts.looks_like_refusal(out, source):
            self.usage["refusals"] += 1
            _s, neutral = prompts.neutral_prompt(source, source_lang, target_lang)
            out = prompts.clean_output(self.chat("", neutral, temperature=0.3, max_tokens=mt, on_delta=on_delta))
            if prompts.looks_like_refusal(out, source):
                raise prompts.RefusalError(out[:200] or "empty response")
        return out


def is_mt_name(name: str) -> bool:
    return is_mt_model(name)
