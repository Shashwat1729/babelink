"""Confidence-based escalation (spec §10).

Signals are deterministic and measurable, not the model's self-report:
- deterministic QA failures/warnings on this segment
- very short or very long source segments
- numeric density (numbers easy to break)
- glossary term density
- roundtrip_chrf below the validated threshold (estimate.ROUNDTRIP_THRESHOLD;
  wired only because the mock fixture AUC > 0.8 — see estimate.fixture_auc)
"""
from __future__ import annotations

import re
from typing import Optional

from .estimate import ROUNDTRIP_MIN_SOURCE_LEN, ROUNDTRIP_THRESHOLD

_NUM = re.compile(r"\d+")


def escalation_needed(source: str, translated: str, qa_types: list[str], glossary_hits: int,
                      *, roundtrip_chrf: Optional[float] = None,
                      roundtrip_threshold: float = ROUNDTRIP_THRESHOLD) -> tuple[bool, str]:
    if "missing_translation" in qa_types or "untranslated_source" in qa_types:
        return True, "qa failure"
    if "number_mismatch" in qa_types:
        return True, "number mismatch"
    if "term_variant_leak" in qa_types:
        return True, "term variant leak"
    if "antecedent_check" in qa_types:
        return True, "possible lost antecedent"
    if "voice_register_drift" in qa_types:
        return True, "register drift"
    if roundtrip_chrf is not None and len(source.strip()) >= ROUNDTRIP_MIN_SOURCE_LEN and roundtrip_chrf < roundtrip_threshold:
        return True, "low roundtrip"
    if len(source) > 500:
        return True, "long segment"
    if len(source) < 5:
        return True, "very short"
    if glossary_hits and glossary_hits >= 3:
        return True, "dense terminology"
    if _NUM.findall(source) and not any(n in translated for n in _NUM.findall(source)):
        return True, "numbers dropped"
    return False, ""
