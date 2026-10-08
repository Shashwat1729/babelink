"""Prompt composition (spec §64-65): every prompt is built from named, versioned parts.

PROMPT_VERSION is recorded on each model run so output changes can be traced to prompt changes.
"""
from __future__ import annotations

import json
import re

PROMPT_VERSION = "t3"

LANG_NAMES = {
    "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "en": "English", "hi": "Hindi",
    "es": "Spanish", "fr": "French", "de": "German", "ru": "Russian", "pt": "Portuguese",
}

STYLE_RULES = {
    "literal": "Stay close to the source wording and structure; do not paraphrase.",
    "faithful-natural": "Faithful to the meaning, but phrase it as natural, idiomatic prose a native reader would enjoy.",
    "literary": "Polished literary prose: vary sentence rhythm, keep the voice and imagery, avoid translationese.",
    "highly-literary": "Highly literary prose with rich diction and rhythm, while never changing facts, names or events.",
}

SYSTEM = (
    "You are a professional literary translator and editor working on a published work of fiction. "
    "You translate faithfully and completely: never summarize, censor, add, or omit content, and never "
    "explain or comment. You keep names, terminology, pronouns, speakers and numbers consistent."
)

_REFUSAL = re.compile(
    r"^\W*(i['’]?m sorry|i am sorry|sorry[,.]|i can(?:'|’)?t|i cannot|i can not|i won['’]?t|i will not|"
    r"as an ai|i['’]?m unable|i am unable|unable to (?:assist|help|comply|translate))",
    re.IGNORECASE,
)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_LABEL = re.compile(r"^\s*(?:translation|translated text|refined translation|output)\s*[:：]\s*", re.IGNORECASE)


class ProviderError(RuntimeError):
    """Backend unreachable / misconfigured: abort the run (state is checkpointed, resumable)."""


class RefusalError(RuntimeError):
    """Model refused an ordinary translation even after the neutral retry (spec §63)."""


def clean_output(text: str) -> str:
    text = _THINK.sub("", text or "")
    text = re.sub(r"^<think>.*", "", text, flags=re.DOTALL | re.IGNORECASE)  # unterminated think block
    text = text.strip()
    text = re.sub(r"^```\w*\n|\n```$", "", text).strip()
    return _LABEL.sub("", text).strip()


def looks_like_refusal(text: str, source: str = "") -> bool:
    t = clean_output(text)
    if not t:
        return True
    return bool(_REFUSAL.match(t)) and len(t) < max(400, len(source) * 3)


def _glossary_block(glossary: dict[str, str] | None, source: str) -> str:
    """Only terms that occur in this passage: keeps prompts small on 100+ term projects."""
    hits = {k: v for k, v in (glossary or {}).items() if k in source}
    if not hits:
        return ""
    return "\n[Glossary — use these renderings exactly]\n" + "\n".join(f"- {k} -> {v}" for k, v in hits.items()) + "\n"


def translation_prompt(source: str, *, glossary=None, context: str = "", style: str = "", source_lang: str = "zh",
                       target_lang: str = "en", tm_hints: list[tuple[str, str]] | None = None) -> tuple[str, str]:
    src, tgt = LANG_NAMES.get(source_lang, source_lang), LANG_NAMES.get(target_lang, target_lang)
    rule = STYLE_RULES.get(style or "faithful-natural", style)
    parts = [
        f"Translate the passage below from {src} into {tgt}. {rule}",
        "Translate every sentence. Keep dialogue as dialogue. Output ONLY the translation of the passage, "
        "with no notes, labels or quotation of the context.",
    ]
    parts.append(_glossary_block(glossary, source))
    if context:
        parts.append(f"[Story context — for reference only; do not translate or repeat it]\n{context}\n")
    if tm_hints:
        parts.append("[Similar lines translated earlier — reference only]\n" + "\n".join(f"{s} => {t}" for s, t in tm_hints) + "\n")
    parts.append(f"[Passage]\n{source}")
    return SYSTEM, "\n".join(p for p in parts if p)


def neutral_prompt(source: str, source_lang: str = "zh", target_lang: str = "en") -> tuple[str, str]:
    """Plain retry prompt after a refusal: same task, no extras (spec §63: no jailbreaks)."""
    src, tgt = LANG_NAMES.get(source_lang, source_lang), LANG_NAMES.get(target_lang, target_lang)
    return ("You are a translation engine for a published work of fiction. Output only the translation.",
            f"Translate this fictional passage from {src} to {tgt}:\n\n{source}")


def refinement_prompt(source: str, draft: str, *, style: str = "", glossary=None, source_lang: str = "zh",
                      target_lang: str = "en") -> tuple[str, str]:
    tgt = LANG_NAMES.get(target_lang, target_lang)
    rule = STYLE_RULES.get(style or "faithful-natural", style)
    user = (
        f"Improve this {tgt} draft translation so it reads as natural prose. {rule}\n"
        "Do NOT change names, glossary terms, numbers, facts, speakers, or relationships; do not add or "
        "remove content. Fix awkward literal phrasing, rhythm, repetition and punctuation only. "
        "Output ONLY the improved translation.\n"
        f"{_glossary_block(glossary, source)}\n[Source]\n{source}\n\n[Draft]\n{draft}"
    )
    return SYSTEM, user


def review_prompt(source: str, translation: str) -> tuple[str, str]:
    return ("You are a translation QA checker.",
            "Compare source and translation. Check meaning, omissions, additions, pronouns/speakers, names. "
            'Return ONLY a JSON array of findings, each {"severity": "warning|error", "type": "...", "message": "..."}. '
            f"If there are none, return [].\n\n[Source]\n{source}\n\n[Translation]\n{translation}")


def terms_prompt(items: list[tuple[str, str]], source_lang: str = "zh", target_lang: str = "en") -> tuple[str, str]:
    """items: (candidate, example sentence). One call classifies + translates a batch."""
    src, tgt = LANG_NAMES.get(source_lang, source_lang), LANG_NAMES.get(target_lang, target_lang)
    lines = "\n".join(f"{i + 1}. {term}  (example: {ex[:80]})" for i, (term, ex) in enumerate(items))
    return ("You are a terminologist for fiction translation.",
            f"These {src} strings recur in a novel. Keep ONLY real proper nouns / fixed terms: character names, "
            "places, organizations, titles, ranks, cultivation realms, techniques, items. Drop ordinary words, "
            "sentence fragments and partial names.\n"
            f"For each kept term give a canonical {tgt} rendering, capitalized like a proper noun (personal names: "
            "pinyin/romanization with given name capitalized; places, organizations, ranks, realms, techniques, items: "
            "Title Case). Omit anything that would be written in lower case.\n"
            'Return ONLY a JSON array: [{"term": "...", "type": "character|location|organization|title|realm|'
            'technique|item", "translation": "..."}].\n\n' + lines)


def summary_prompt(text: str, target_lang: str = "en") -> tuple[str, str]:
    tgt = LANG_NAMES.get(target_lang, target_lang)
    return ("You summarize fiction chapters for a translation team.",
            f"Summarize this chapter in at most 4 sentences in {tgt}: plot events, characters present, "
            f"locations, unresolved threads. No commentary.\n\n{text}")


def parse_json_array(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", clean_output(text), re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
