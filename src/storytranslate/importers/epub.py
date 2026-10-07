"""EPUB importer: chapters from spine items, segments from paragraphs."""
from __future__ import annotations

from pathlib import Path

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

from ..db import Database
from ..models import Chapter, Segment, SegmentState
from ..segment import paragraphs_from_html


def import_epub(db: Database, project_id: int, path: str | Path) -> int:
    path = Path(path)
    book = epub.read_epub(str(path))
    doc_id = db.add_document(project_id, str(path), "epub", path.stem)
    order = 0
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        if isinstance(item, epub.EpubNav) or "nav" in getattr(item, "properties", []):
            continue
        html = item.get_content().decode("utf-8", errors="replace")
        paras = paragraphs_from_html(html)
        if not paras:
            continue
        title = ""
        soup = BeautifulSoup(html, "lxml")
        heading = soup.find(["h1", "h2", "h3", "title"])
        if heading:
            title = heading.get_text(" ", strip=True)
        ch_id = db.add_chapter(Chapter(None, project_id, doc_id, order, title or f"Chapter {order + 1}", html, None))
        for j, para in enumerate(paras):
            db.add_segment(Segment(None, project_id, doc_id, ch_id, None, j, para, None, SegmentState.UNPROCESSED))
        order += 1
    return doc_id
