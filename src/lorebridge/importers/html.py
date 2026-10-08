"""Saved-HTML importer (spec §70): one file, chapters from headings."""
from __future__ import annotations

from pathlib import Path

from ..db import Database
from ..segment import chapters_from_stream, stream_from_html, strip_boilerplate
from .base import read_text_file, store_chapters


def import_html(db: Database, project_id: int, path: str | Path) -> int:
    path = Path(path)
    doc_id = db.add_document(project_id, str(path), "html", path.stem)
    items = strip_boilerplate(stream_from_html(read_text_file(path)))
    store_chapters(db, project_id, doc_id, chapters_from_stream(items, default_title=path.stem))
    return doc_id
