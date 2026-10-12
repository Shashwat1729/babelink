"""Hy-MT2 (Tencent Hunyuan-MT2) prompt format.

Official instruction format (https://github.com/Tencent-Hunyuan/Hy-MT2):
default translation, terminology references ("X translates to Y"), style
constraint, and background information. The models have NO default system
prompt, so the system message is empty and everything rides in one user
message. Temperature 0.7 / top_p 0.6 / top_k 20 / repetition_penalty 1.05
are the released inference settings for the 1.8B/7B sizes.

Why a separate builder instead of reusing prompts.translation_prompt:
a translation-only model was never trained on chatty multi-section prompts
("Story context - for reference only; do not translate or repeat it",
"Similar lines translated earlier", "Output ONLY..."). Feeding it the
general-LLM prompt degrades quality and glossary adherence; the official
terminology + background blocks are what it was trained to follow.
"""
from __future__ import annotations

# Full language names: the card requires full names, not codes.
FULL_NAMES = {
    "zh": "Chinese", "zh-Hant": "Traditional Chinese", "yue": "Cantonese",
    "en": "English", "ja": "Japanese", "ko": "Korean", "fr": "French",
    "pt": "Portuguese", "es": "Spanish", "tr": "Turkish", "ru": "Russian",
    "ar": "Arabic", "th": "Thai", "it": "Italian", "de": "German",
    "vi": "Vietnamese", "ms": "Malay", "id": "Indonesian", "tl": "Filipino",
    "hi": "Hindi", "pl": "Polish", "cs": "Czech", "nl": "Dutch",
    "km": "Khmer", "my": "Burmese", "fa": "Persian", "gu": "Gujarati",
    "ur": "Urdu", "te": "Telugu", "mr": "Marathi", "he": "Hebrew",
    "bn": "Bengali", "ta": "Tamil", "uk": "Ukrainian", "bo": "Tibetan",
    "kk": "Kazakh", "mn": "Mongolian", "ug": "Uyghur",
}

_MT_MARKERS = ("hy-mt", "hymt", "hymt2", "hy_mt", "hunyuan-mt", "hy-mt2")

# Created locally so Ollama's template engine emits the model's real special
# tokens (raw-text prompts BPE-fragment them and produce garbage):
#   ollama create hymt2-1.8b -f Modelfile.hymt2
DEFAULT_MODEL = "hymt2-1.8b"

# Exact chat template from tencent/Hy-MT2-1.8B (chat_template.jinja). Ollama's
# GGUF import exposes this model as completion-only with a wrong stop token,
# so the provider wraps the prompt manually and stops on the real EOS.
BOS = "<｜hy_begin▁of▁sentence｜>"
USER = "<｜hy_User｜>"
ASSISTANT = "<｜hy_Assistant｜>"
EOS = "<｜hy_place▁holder▁no▁2｜>"
EOT = "<｜hy_EOT｜>"


def wrap_prompt(user_text: str, system_text: str = "") -> str:
    """Replicate chat_template.jinja for a single user message (+ generation prompt)."""
    parts = [BOS]
    if system_text:
        parts.append(f"{system_text}<｜hy_place▁holder▁no▁3｜>")
    return "".join(parts) + f"{USER}{user_text}{ASSISTANT}"


def unwrap_output(text: str) -> str:
    """Strip stop/EOT leftovers and any thinking trace the fast-thinking mode emits."""
    import re

    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    for tok in (EOS, EOT, "<｜hy_Assistant｜>", "<｜hy_User｜>", BOS, "</think>", "<answer>", "</answer>"):
        text = text.replace(tok, "")
    return text.strip()

# Released sampling settings for Hy-MT2 1.8B/7B.
SAMPLING = {"temperature": 0.7, "top_p": 0.6, "top_k": 20, "repetition_penalty": 1.05}

_STYLE_LINE = {
    # literal/faithful map to the default instruction (no extra style demand).
    "literal": "",
    "faithful-natural": "",
    "literary": "Note that the translation style must strictly conform to [elegant literary prose]:",
    "highly-literary": "Note that the translation style must strictly conform to [highly literary prose with rich diction and rhythm]:",
}


def is_mt_model(name: str) -> bool:
    n = (name or "").lower()
    return any(m in n for m in _MT_MARKERS)


def full_name(code: str) -> str:
    return FULL_NAMES.get(code, code)


def mt_prompt(source: str, *, glossary: dict[str, str] | None = None, style: str = "",
              source_lang: str = "zh", target_lang: str = "en", background: str = "") -> tuple[str, str]:
    """Return (system, user) in the official Hy-MT2 format. System is empty:
    these models were released with no default system prompt."""
    tgt = full_name(target_lang)
    parts: list[str] = []
    hits = {k: v for k, v in (glossary or {}).items() if k in source}
    if hits:  # official terminology block
        parts.append("Reference the following translations:\n" +
                     "\n".join(f"{k} translates to {v}" for k, v in hits.items()))
    if background:
        parts.append(f"[Background Information]\n{background}\n\n"
                     f"Please translate the following text into {tgt}, taking the provided "
                     f"background information into consideration.\n\n[Source Text]\n{source}")
    else:
        style_line = _STYLE_LINE.get(style or "", "")
        if style_line:
            parts.append(f"Please translate the following text into {tgt}. {style_line}\n\n{source}")
        else:
            parts.append(f"Translate the following text into {tgt}. Note that you should "
                         f"only output the translated result without any additional explanation:\n\n{source}")
    return "", "\n\n".join(parts)
