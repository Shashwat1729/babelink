"""QA: deterministic checks (spec §36) + optional AI review (spec §37).

Deterministic checks are cheap, always run, and replace their own earlier findings (no duplicate rows).
Only `error` findings move a segment to NEEDS_REVIEW; warnings are listed but do not block.
"""
from __future__ import annotations

import re
from typing import Optional

from .db import Database
from .models import SegmentState

_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")
# A quoted inscription with a parenthetical gloss ("敕造宁國府" (Built by
# imperial order…)) is translated, not leftover: the CJK is the quoted source.
_GLOSSED = re.compile(r"[\"“「『][一-鿿぀-ヿ가-힯]+[\"”」』]\s*\([^()]*\)")
_NUM = re.compile(r"\d+")
_LEAK = re.compile(r"<think>|\[(?:Passage|Glossary|Story context|Draft|Source)\b|^\s*(?:Translation|Output)\s*:", re.IGNORECASE | re.MULTILINE)
# Unmapped entity proxies (?E200?, [E4], @@E3@@, __E1__, ⟦E2⟧) are never
# legitimate English — the model rewrote or invented them instead of passing
# them through (Red Chamber DG18: ?E200?). An error, not a warning: the
# segment needs re-translation, and the finding routes it there.
_PROXY_REMANT = re.compile(r"\u27e6E\d+\u27e7|@@E\d+@@|__E\d__|\[E\d+\]|\?E\d+\?")
_LOOP = re.compile(r"(\b\w+(?:\W+\w+){2,5}\b)(?:\W+\1){3,}", re.IGNORECASE)

DETERMINISTIC = ("missing_translation", "prompt_leak", "proxy_remnant", "untranslated_source", "number_mismatch", "suspicious_omission",
                 "suspicious_expansion", "repetition", "term_not_applied", "truncated_ending")


def check_segment(source: str, translated: str, source_lang: str = "zh", glossary: Optional[dict[str, str]] = None) -> list[tuple[str, str, str]]:
    """-> [(severity, type, message)]. Pure function: easy to unit-test."""
    if not translated or not translated.strip():
        return [("error", "missing_translation", "Segment has no translation.")]
    found: list[tuple[str, str, str]] = []
    if _LEAK.search(translated):
        found.append(("error", "prompt_leak", "Output contains prompt or reasoning markup."))
    if _PROXY_REMANT.search(translated):
        found.append(("error", "proxy_remnant", "Output contains an unmapped entity proxy (?E200?/[E4]); re-translate."))
    cjk_source = bool(_CJK.search(source))
    if cjk_source and _CJK.search(_GLOSSED.sub("", translated)):
        found.append(("warning", "untranslated_source", "Translation still contains CJK characters."))
    src_nums = _NUM.findall(source)
    if src_nums and not all(n in translated for n in src_nums):
        found.append(("warning", "number_mismatch", f"Numbers {src_nums} not all present in translation."))
    # English is ~2.5x longer than Chinese by characters, so the thresholds depend on the script
    lo, hi = (0.8, 12.0) if cjk_source else (0.3, 4.0)
    if len(source) >= 12 and len(translated) < len(source) * lo:
        found.append(("warning", "suspicious_omission", "Translation much shorter than source."))
    if len(translated) > max(80, len(source) * hi):
        found.append(("warning", "suspicious_expansion", "Translation much longer than source."))
    if _LOOP.search(translated):
        found.append(("warning", "repetition", "Translation repeats the same phrase."))
    if len(source) >= 60 and not re.search(r"[.!?…?\"”」』)\]]\s*$", translated):
        # Rashomon CAT: a 322-char segment stopped mid-word ('religious ') at
        # 1/5 of its token budget — early EOS, not a budget cutoff. Length
        # ratios miss it (output was longer than source); the missing terminal
        # punctuation is the tell. Warning: flags for re-translation review.
        found.append(("warning", "truncated_ending", "Translation ends without terminal punctuation (possible early stop)."))
    low = translated.lower()
    for src, tgt in (glossary or {}).items():
        if src in source and tgt.lower() not in low:
            found.append(("warning", "term_not_applied", f"'{src}' should be rendered '{tgt}'."))
    return found


def qa_segment(db: Database, project_id: int, segment_id: int, source: str, translated: str, source_lang: str = "zh",
               glossary: Optional[dict[str, str]] = None) -> list[str]:
    """Re-run deterministic checks for one segment. Returns the finding types."""
    db.clear_qa(segment_id, DETERMINISTIC)
    findings = check_segment(source, translated, source_lang, glossary)
    for sev, typ, msg in findings:
        db.add_qa(project_id, segment_id, sev, typ, msg)
    return [t for _, t, _ in findings]


def run_qa(db: Database, project_id: int, glossary: Optional[dict[str, str]] = None, chapter_id: Optional[int] = None) -> dict:
    """QA stage over translated segments: errors -> NEEDS_REVIEW, otherwise TRANSLATED/REFINED -> QA_PASSED.
    HUMAN_APPROVED segments are checked but never moved."""
    project = db.get_project(project_id)
    q = ("SELECT s.* FROM segments s JOIN chapters c ON c.id=s.chapter_id WHERE s.project_id=? "
         "AND s.translated_text IS NOT NULL")
    args: list = [project_id]
    if chapter_id is not None:
        q += " AND s.chapter_id=?"
        args.append(chapter_id)
    stats = {"checked": 0, "passed": 0, "needs_review": 0, "warnings": 0}
    for s in db.conn.execute(q + " ORDER BY c.order_index, s.order_index", args).fetchall():
        sevs = {(sev) for sev, _, _ in check_segment(s["source_text"], s["translated_text"], project["source_lang"], glossary)}
        qa_segment(db, project_id, s["id"], s["source_text"], s["translated_text"], project["source_lang"], glossary)
        stats["checked"] += 1
        stats["warnings"] += "warning" in sevs
        if s["state"] == SegmentState.HUMAN_APPROVED.value:
            continue
        if "error" in sevs:
            db.update_segment(s["id"], state=SegmentState.NEEDS_REVIEW)
            stats["needs_review"] += 1
        elif s["state"] in (SegmentState.TRANSLATED.value, SegmentState.REFINED.value, SegmentState.NEEDS_REVIEW.value, SegmentState.QA_PASSED.value):
            db.update_segment(s["id"], state=SegmentState.QA_PASSED)
            stats["passed"] += 1
    return stats


def ai_qa(db: Database, project_id: int, provider, *, limit: int = 50, chapter_id: Optional[int] = None) -> int:
    """Run provider.review on translated segments; store structured findings (spec §37).
    Earlier AI findings for a segment are replaced, not appended."""
    q = "SELECT * FROM segments WHERE project_id=? AND translated_text IS NOT NULL"
    args: list = [project_id]
    if chapter_id is not None:
        q += " AND chapter_id=?"
        args.append(chapter_id)
    n = 0
    for s in db.conn.execute(q + " ORDER BY chapter_id, order_index LIMIT ?", (*args, limit)).fetchall():
        db.conn.execute("DELETE FROM qa_results WHERE segment_id=? AND type LIKE 'ai_%'", (s["id"],))
        try:
            findings = provider.review(s["source_text"], s["translated_text"])
        except Exception as exc:  # provider down/misconfigured -> deterministic findings remain
            db.add_qa(project_id, s["id"], "error", "ai_qa_failed", str(exc))
            continue
        for f in findings:
            db.add_qa(project_id, s["id"], f.get("severity", "warning"), "ai_" + str(f.get("type", "issue")).removeprefix("ai_"), f.get("message", ""))
        n += 1
    db.conn.commit()
    return n
