"""TXT importer: chapters from headings, segments from paragraphs."""
from __future__ import annotations

from pathlib import Path

from ..db import Database
from ..segment import chapters_from_text
from .base import read_text_file, store_chapters


def import_txt(db: Database, project_id: int, path: str | Path) -> int:
    path = Path(path)
    doc_id = db.add_document(project_id, str(path), "txt", path.stem)
    store_chapters(db, project_id, doc_id, chapters_from_text(read_text_file(path), default_title=path.stem))
    return doc_id
