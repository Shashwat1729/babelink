"""Shared importer plumbing: decode files, append chapters to a project."""
from __future__ import annotations

from pathlib import Path

from ..db import Database
from ..models import Chapter, Segment, SegmentState
from ..segment import ParsedChapter


def read_text_file(path: str | Path) -> str:
    """Web-novel TXT files are often GBK/Big5; try UTF-8 first, then CJK codecs."""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def store_chapters(db: Database, project_id: int, doc_id: int, chapters: list[ParsedChapter]) -> list[int]:
    """Append chapters after the project's existing ones (imports and pastes accumulate)."""
    base = db.conn.execute(
        "SELECT COALESCE(MAX(order_index), -1) + 1 AS b FROM chapters WHERE project_id=?", (project_id,)
    ).fetchone()["b"]
    ids = []
    for i, ch in enumerate(chapters):
        ch_id = db.add_chapter(Chapter(None, project_id, doc_id, base + i, ch.title, ch.html, None))
        for j, para in enumerate(ch.paragraphs):
            db.add_segment(Segment(None, project_id, doc_id, ch_id, None, j, para, None, SegmentState.UNPROCESSED))
        ids.append(ch_id)
    return ids
