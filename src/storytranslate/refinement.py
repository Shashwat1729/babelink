"""Literary refinement pass with guardrails (spec §30)."""
from __future__ import annotations

import json
import re
from typing import Optional

from .db import Database
from .models import SegmentState
from .providers.base import LLMProvider

_NUM = re.compile(r"\d+")


def guardrail_ok(source: str, draft: str, refined: str, canonical_terms: list[str]) -> tuple[bool, str]:
    # canonical terms in draft must survive refinement
    for term in canonical_terms:
        if term in draft and term not in refined:
            return False, f"canonical term lost: {term}"
    # numbers must survive
    src_nums = _NUM.findall(source)
    if src_nums and not all(n in refined for n in src_nums):
        return False, "number changed or dropped"
    # length sanity: refined must not be tiny compared to source
    if source and len(refined) < len(source) * 0.3:
        return False, "suspiciously short"
    return True, ""


def run_refinement(db: Database, project_id: int, provider: LLMProvider, project_dir: str, *, limit: Optional[int] = None) -> int:
    project = db.get_project(project_id)
    from .story_bible import load_section

    sg = load_section(project_dir, "style_guide") if project_dir else {}
    style = sg.get("preset", "") if isinstance(sg, dict) else ""
    terms = db.terminology_for_project(project_id)
    canonical: list[str] = []
    for t in terms:
        canon = json.loads(t["canonical_translation"])
        if project["target_lang"] in canon:
            canonical.append(canon[project["target_lang"]])

    segs = db.conn.execute(
        "SELECT * FROM segments WHERE project_id=? AND state='TRANSLATED' ORDER BY chapter_id, order_index",
        (project_id,),
    ).fetchall()
    if limit is not None:
        segs = segs[:limit]
    done = 0
    for s in segs:
        draft = s["translated_text"] or ""
        refined = provider.refine(s["source_text"], draft, style=style)
        ok, reason = guardrail_ok(s["source_text"], draft, refined, canonical)
        if ok:
            db.update_segment(s["id"], translated=refined, state=SegmentState.REFINED)
        else:
            db.add_qa(project_id, s["id"], "warning", "refinement_guardrail", reason)
            db.update_segment(s["id"], state=SegmentState.NEEDS_REVIEW)
        done += 1
    return done


def mark_stale_by_term(db: Database, project_id: int, source_form: str, new_translation: str) -> int:
    """Update canonical translation, then mark affected segments STALE (spec §39)."""
    rows = db.terminology_for_project(project_id)
    for r in rows:
        forms = json.loads(r["source_forms"])
        if source_form in forms:
            canon = json.loads(r["canonical_translation"])
            for lang in list(canon.keys()):
                canon[lang] = new_translation
            db.conn.execute(
                "UPDATE terminology SET canonical_translation=? WHERE id=?",
                (json.dumps(canon, ensure_ascii=False), r["id"]),
            )
    affected = db.conn.execute(
        "SELECT id FROM segments WHERE project_id=? AND source_text LIKE ? AND translated_text IS NOT NULL",
        (project_id, f"%{source_form}%"),
    ).fetchall()
    for row in affected:
        db.update_segment(row["id"], state=SegmentState.STALE)
    db.conn.commit()
    return len(affected)
