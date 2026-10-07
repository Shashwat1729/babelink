"""End-to-end pipeline: analyze + translate with checkpointing (spec §28, §41)."""
from __future__ import annotations

import json
from typing import Optional

from .db import Database
from .langdetect import detect_language
from .models import ModelRun, SegmentState, TMEntry, TerminologyEntry
from .providers.base import LLMProvider
from .qa import qa_segment
from .terminology import mine_terms


def analyze_project(db: Database, project_id: int) -> dict:
    """Detect language, mine terminology, mark segments ANALYZED."""
    segs = db.conn.execute(
        "SELECT * FROM segments WHERE project_id=? ORDER BY chapter_id, order_index", (project_id,)
    ).fetchall()
    if not segs:
        return {"language": "und", "segments": 0}
    all_text = "\n".join(s["source_text"] for s in segs[:200])
    lang = detect_language(all_text)
    db.conn.execute("UPDATE projects SET source_lang=? WHERE id=?", (lang, project_id))
    db.conn.commit()
    texts = [s["source_text"] for s in segs]
    for term, count in mine_terms(texts, min_count=3, top=50):
        existing = db.conn.execute(
            "SELECT id FROM terminology WHERE project_id=? AND source_forms LIKE ?",
            (project_id, f'%"{term}"%'),
        ).fetchone()
        if not existing:
            db.add_terminology(TerminologyEntry(None, project_id, [term], {}, concept="", policy="preferred", status="suggested", occurrences=count))
    for s in segs:
        if s["state"] == SegmentState.UNPROCESSED.value:
            db.update_segment(s["id"], state=SegmentState.ANALYZED)
    return {"language": lang, "segments": len(segs)}


def translate_project(db: Database, project_id: int, provider: LLMProvider, *, limit: Optional[int] = None) -> int:
    """Translate segments that still need it. Checkpoints after every segment."""
    project = db.get_project(project_id)
    run_id = db.add_model_run(ModelRun(None, project_id, "translation", provider.name, "v1", "", "", "", "{}"))
    terms = db.terminology_for_project(project_id)
    glossary: dict[str, str] = {}
    for t in terms:
        srcs = json.loads(t["source_forms"])
        tgts = json.loads(t["canonical_translation"])
        if tgts and project["target_lang"] in tgts:
            for src in srcs:
                glossary[src] = tgts[project["target_lang"]]
    pending = db.open_segments(project_id)
    if limit is not None:
        pending = pending[:limit]
    done = 0
    for s in pending:
        tm = db.tm_lookup(project_id, s["source_text"])
        if tm:
            translated = tm["translated_text"]
        else:
            translated = provider.translate(s["source_text"], glossary=glossary)
            db.add_tm(TMEntry(None, project_id, s["source_text"], translated, model=provider.name, chapter_id=s["chapter_id"]))
        db.update_segment(s["id"], translated=translated, state=SegmentState.TRANSLATED, model_run_id=run_id)
        qa_segment(db, project_id, s["id"], s["source_text"], translated, project["source_lang"])
        done += 1
    return done
