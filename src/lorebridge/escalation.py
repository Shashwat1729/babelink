"""Confidence-based escalation (spec §10).

Signals are deterministic and measurable, not the model's self-report:
- deterministic QA failures/warnings on this segment
- very short or very long source segments
- numeric density (numbers easy to break)
- glossary term density
"""
from __future__ import annotations

import re

_NUM = re.compile(r"\d+")


def escalation_needed(source: str, translated: str, qa_types: list[str], glossary_hits: int) -> tuple[bool, str]:
    if "missing_translation" in qa_types or "untranslated_source" in qa_types:
        return True, "qa failure"
    if "number_mismatch" in qa_types:
        return True, "number mismatch"
    if len(source) > 500:
        return True, "long segment"
    if len(source) < 5:
        return True, "very short"
    if glossary_hits and glossary_hits >= 3:
        return True, "dense terminology"
    if _NUM.findall(source) and not any(n in translated for n in _NUM.findall(source)):
        return True, "numbers dropped"
    return False, ""
