"""Literary refinement pass with guardrails (spec §30) and term-driven retranslation (spec §39)."""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

from . import prompts
from .db import Database
from .models import ModelRun, SegmentState
from .qa import qa_segment
from .terminology import build_glossary

_NUM = re.compile(r"\d+")
_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")


def _split_note(refined: str) -> tuple[str, str]:
    """A trailing 'NOTE: ...' line from the refiner is a translator's note, not prose."""
    lines = refined.strip().splitlines()
    if lines and lines[-1].strip().upper().startswith("NOTE:"):
        note = lines[-1].strip()[5:].strip()
        return "\n".join(lines[:-1]).strip(), note[:500]
    return refined, ""


_CAPPED = re.compile(r"(?<![A-Za-z])([A-Z][a-z]{2,})(?![A-Za-z])")  # ASCII edges: \b treats CJK as word chars


def dropped_names(draft: str, refined: str) -> list[str]:
    """Capitalized mid-sentence words the refinement lost ('Ruhai' -> 'Ruihai',
    'Rugui' -> 'Rugu'): almost always a corrupted name, never a style edit.
    Sentence-initial words are skipped (rephrasing moves them legitimately)."""
    mids: set[str] = set()
    for sent in re.split(r"(?<=[.!?])\s+", draft):
        mids.update(_CAPPED.findall(sent)[1:])
    return sorted(w for w in mids if w not in refined)


def guardrail_ok(source: str, draft: str, refined: str, canonical_terms: list[str]) -> tuple[bool, str]:
    """A refinement may only change style: never terms, numbers, script, or the amount of content."""
    if not refined.strip():
        return False, "empty refinement"
    for term in canonical_terms:  # canonical terms in draft must survive refinement
        if term in draft and term not in refined:
            return False, f"canonical term lost: {term}"
    src_nums = _NUM.findall(source)
    if src_nums and not all(n in refined for n in src_nums):
        return False, "number changed or dropped"
    if source and len(refined) < len(source) * 0.3:
        return False, "suspiciously short"
    if draft and not (0.6 <= len(refined) / len(draft) <= 1.6):
        return False, "length changed too much (content added or removed)"
    if _CJK.search(refined) and not _CJK.search(draft):
        return False, "source-language characters introduced"
    if prompts.looks_like_refusal(refined, draft):
        return False, "model refused"
    return True, ""


REFINABLE = ("TRANSLATED", "QA_PASSED")  # a paragraph a person has since edited or approved is no longer refined


def run_refinement(db: Database, project_id: int, provider, project_dir: str = "", *, limit: Optional[int] = None,
                   chapter_id: Optional[int] = None, on_progress: Optional[Callable[[int, int, str], None]] = None,
                   should_stop: Optional[Callable[[], bool]] = None) -> int:
    """Refine TRANSLATED / QA_PASSED segments. A refinement that violates a guardrail is discarded
    (the draft stays) and the segment is flagged NEEDS_REVIEW."""
    project = db.get_project(project_id)
    style = project["style"] or "faithful-natural"
    glossary = build_glossary(db, project_id)
    q = ("SELECT s.* FROM segments s JOIN chapters c ON c.id=s.chapter_id WHERE s.project_id=? "
         f"AND s.state IN {REFINABLE} AND s.translated_text IS NOT NULL")
    args: list = [project_id]
    if chapter_id is not None:
        q += " AND s.chapter_id=?"
        args.append(chapter_id)
    segs = db.conn.execute(q + " ORDER BY c.order_index, s.order_index", args).fetchall()
    if limit is not None:
        segs = segs[:limit]
    run_id = db.add_model_run(ModelRun(None, project_id, "refinement", provider.name, prompts.PROMPT_VERSION, "", "", "",
                                       json.dumps({"style": style})))
    done = rejected = 0
    for s in segs:
        if should_stop and should_stop():
            break
        if db.get_segment(s["id"])["state"] not in REFINABLE:  # a person edited or approved it since the batch was picked
            continue
        draft = s["translated_text"]
        hits = {k: v for k, v in glossary.items() if k in s["source_text"]}
        refined = provider.refine(s["source_text"], draft, style=style, glossary=hits,
                                  source_lang=project["source_lang"], target_lang=project["target_lang"])
        refined, note = _split_note(refined)  # a trailing NOTE: line is a translator's note, stored apart
        ok, reason = guardrail_ok(s["source_text"], draft, refined, list(hits.values()))
        if ok:
            if db.update_segment(s["id"], translated=refined, state=SegmentState.REFINED, model_run_id=run_id, note=note or None,
                                 only_if_state=REFINABLE):
                qa_segment(db, project_id, s["id"], s["source_text"], refined, project["source_lang"], hits)
                for lost in dropped_names(draft, refined):
                    db.add_qa(project_id, s["id"], "warning", "possible_name_change",
                              f"'{lost}' vanished in refinement — check the name")
        else:
            rejected += 1
            if db.update_segment(s["id"], state=SegmentState.NEEDS_REVIEW, only_if_state=REFINABLE):
                db.clear_qa(s["id"], ("refinement_guardrail",))
                db.add_qa(project_id, s["id"], "warning", "refinement_guardrail", reason)
        done += 1
        if on_progress:
            on_progress(done, len(segs), s["source_text"][:60])
    db.finish_model_run(run_id, {"style": style, "refined": done - rejected, "rejected": rejected,
                                 "usage": dict(getattr(provider, "usage", {}))})
    return done


def propagation_impact(db: Database, project_id: int, source_form: str) -> dict:
    """Preview of a term change, git-status style: which translated segments in
    which chapters would go STALE (humans' approved work excluded). Read this
    before confirming a retranslation."""
    rows = db.conn.execute(
        "SELECT s.id, c.title FROM segments s JOIN chapters c ON c.id=s.chapter_id "
        "WHERE s.project_id=? AND instr(s.source_text, ?)>0 AND s.translated_text IS NOT NULL "
        "AND s.state<>'HUMAN_APPROVED' ORDER BY c.order_index, s.order_index",
        (project_id, source_form)).fetchall()
    chapters: dict[str, int] = {}
    for r in rows:
        chapters[r["title"]] = chapters.get(r["title"], 0) + 1
    return {"segments": len(rows), "chapters": chapters,
            "sample": [r["id"] for r in rows[:5]]}


def mark_stale_by_term(db: Database, project_id: int, source_form: str, new_translation: str) -> int:
    """Update canonical translation, then mark affected segments STALE (spec §39).
    HUMAN_APPROVED segments are never touched: human work is not silently overwritten."""
    target = db.get_project(project_id)["target_lang"]
    for r in db.terminology_for_project(project_id):
        if source_form in json.loads(r["source_forms"]):
            canon = json.loads(r["canonical_translation"])
            canon[target] = new_translation
            db.update_terminology(r["id"], canonical_translation=canon, status="confirmed")
    affected = db.conn.execute(
        "SELECT id FROM segments WHERE project_id=? AND instr(source_text, ?)>0 AND translated_text IS NOT NULL AND state<>'HUMAN_APPROVED'",
        (project_id, source_form),
    ).fetchall()
    for row in affected:
        db.update_segment(row["id"], state=SegmentState.STALE)
    return len(affected)
