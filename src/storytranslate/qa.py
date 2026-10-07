"""Deterministic QA checks (spec §36)."""
from __future__ import annotations

import re

from .db import Database
from .models import SegmentState

_CJK = re.compile(r"[一-鿿]")
_NUM = re.compile(r"\d+")


def qa_segment(db: Database, project_id: int, segment_id: int, source: str, translated: str, source_lang: str) -> None:
    if not translated or not translated.strip():
        db.add_qa(project_id, segment_id, "error", "missing_translation", "Segment has no translation.")
        return
    if source_lang == "zh" and _CJK.search(translated):
        db.add_qa(project_id, segment_id, "warning", "untranslated_source", "Translation still contains CJK characters.")
    src_nums = _NUM.findall(source)
    if src_nums and not all(n in translated for n in src_nums):
        db.add_qa(project_id, segment_id, "warning", "number_mismatch", f"Numbers {src_nums} not all present in translation.")
    if len(translated) < len(source) * 0.2:
        db.add_qa(project_id, segment_id, "warning", "suspicious_omission", "Translation much shorter than source.")
