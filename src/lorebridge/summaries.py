"""Extractive chapter/arc summaries (spec §26, §82, §83)."""
from __future__ import annotations

import re
from pathlib import Path

from .db import Database
from .story_bible import load_section, save_section

_SENT_SPLIT = re.compile(r"(?<=[。！？!?.])\s*")


def chapter_summary(texts: list[str], max_sentences: int = 3) -> str:
    sentences: list[str] = []
    for t in texts:
        sentences.extend(s.strip() for s in _SENT_SPLIT.split(t) if s.strip())
    # keep first + most informative sentences (longest) as heuristic
    if not sentences:
        return ""
    head = sentences[:1]
    rest = sorted(sentences[1:], key=len, reverse=True)[: max_sentences - 1]
    return " ".join(head + rest)


def write_summaries(db: Database, project_id: int, project_dir: str | Path) -> None:
    chapters = db.conn.execute(
        "SELECT * FROM chapters WHERE project_id=? ORDER BY order_index", (project_id,)
    ).fetchall()
    chapter_summaries = []
    for ch in chapters:
        summary = ch["summary"]
        if not summary:  # keep model-written summaries (pipeline.translate_project(summarize=True))
            summary = chapter_summary([s["source_text"] for s in db.segments_for_chapter(ch["id"])])
            db.conn.execute("UPDATE chapters SET summary=? WHERE id=?", (summary, ch["id"]))
        chapter_summaries.append({"chapter_id": ch["id"], "title": ch["title"], "summary": summary})
    db.conn.commit()
    save_section(project_dir, "chapter_summaries", chapter_summaries)

    arcs: list[dict] = []
    arc_size = 5
    for i in range(0, len(chapter_summaries), arc_size):
        group = chapter_summaries[i : i + arc_size]
        arcs.append(
            {
                "arc": len(arcs) + 1,
                "titles": [g["title"] for g in group],
                "summary": " ".join(g["summary"] for g in group)[:2000],
            }
        )
    save_section(project_dir, "arc_summaries", arcs)
