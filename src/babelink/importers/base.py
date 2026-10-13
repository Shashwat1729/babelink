"""Shared importer plumbing: decode files, append chapters to a project."""
from __future__ import annotations

from pathlib import Path

from ..db import Database
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
    """Append chapters after the project's existing ones (imports and pastes accumulate).
    Single transaction via bulk_store_chapters: whole-book imports stay fast."""
    return db.bulk_store_chapters(project_id, doc_id, chapters)
