"""EPUB importer: spine-ordered documents -> chapters -> paragraph segments.

Two modes. Normal EPUBs: one chapter per document (title from its heading), original html kept
so export can round-trip. Messy EPUBs (e.g. a Gutenberg text dump chopped into arbitrary files):
if files hold several chapter headings each, chapters follow the headings instead of the files.
"""
from __future__ import annotations

from pathlib import Path

import ebooklib
from ebooklib import epub

from ..db import Database
from ..models import Chapter
from ..segment import ParsedChapter, chapters_from_stream, strip_boilerplate, stream_from_html, to_html
from .base import store_chapters


def _documents(book) -> list:
    docs = []
    for idref, _linear in book.spine:
        item = book.get_item_with_id(idref)
        if item is not None and item.get_type() == ebooklib.ITEM_DOCUMENT:
            docs.append(item)
    if not docs:
        docs = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
    return [d for d in docs if not isinstance(d, epub.EpubNav) and "nav" not in getattr(d, "properties", [])]


def import_epub(db: Database, project_id: int, path: str | Path) -> int:
    path = Path(path)
    book = epub.read_epub(str(path))
    meta = book.get_metadata("DC", "title")
    doc_id = db.add_document(project_id, str(path), "epub", meta[0][0] if meta else path.stem)

    per_item = []
    for item in _documents(book):
        html = item.get_content().decode("utf-8", errors="replace")
        per_item.append((html, stream_from_html(html)))
    flat = strip_boilerplate([x for _, stream in per_item for x in stream])
    headings = sum(1 for kind, _ in flat if kind == "heading")
    most_per_file = max((sum(1 for k, _ in stream if k == "heading") for _, stream in per_item), default=0)

    chapters: list[ParsedChapter] = []
    if headings >= 2 and most_per_file >= 2:
        chapters = chapters_from_stream(flat, default_title="Front matter")
    else:
        for html, stream in per_item:
            stream = strip_boilerplate(stream)
            paras = [t for k, t in stream if k == "para"]
            if not paras:
                continue
            title = next((t for k, t in stream if k == "heading"), "") or f"Chapter {len(chapters) + 1}"
            chapters.append(ParsedChapter(title, html, paras))
    store_chapters(db, project_id, doc_id, chapters)
    return doc_id
