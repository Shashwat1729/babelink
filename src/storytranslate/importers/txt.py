"""TXT importer: chapters from headings, segments from paragraphs."""
from __future__ import annotations

from pathlib import Path

from ..db import Database
from ..models import Chapter, Segment, SegmentState
from ..segment import chapters_from_text


def import_txt(db: Database, project_id: int, path: str | Path) -> int:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    doc_id = db.add_document(project_id, str(path), "txt", path.stem)
    for i, ch in enumerate(chapters_from_text(text)):
        ch_id = db.add_chapter(Chapter(None, project_id, doc_id, i, ch.title, ch.html, None))
        for j, para in enumerate(ch.paragraphs):
            db.add_segment(Segment(None, project_id, doc_id, ch_id, None, j, para, None, SegmentState.UNPROCESSED))
    return doc_id
