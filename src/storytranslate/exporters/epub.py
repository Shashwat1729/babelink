"""Build a translated EPUB from chapter html + translated segments."""
from __future__ import annotations

from pathlib import Path

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

from ..db import Database


def _replace_text_nodes(tag, new_text: str) -> None:
    """Replace a paragraph's text. Preserves the tag + attributes (class/style =
    alignment/spacing). Inline b/i/em tags are NOT preserved yet because we
    translate per-paragraph; see ponytail ceiling note in spec §68."""
    tag.clear()
    tag.append(new_text)


def _replace_paragraphs(html: str, translated: list[str]) -> str:
    soup = BeautifulSoup(html, "lxml")
    tags = soup.find_all(["p", "div", "blockquote", "li"])
    text_tags = [t for t in tags if " ".join(t.get_text(" ", strip=True).split())]
    if len(text_tags) == len(translated):
        for tag, text in zip(text_tags, translated):
            _replace_text_nodes(tag, text)
        return str(soup)
    body = soup.find("body") or soup
    body.clear()
    for text in translated:
        p = soup.new_tag("p")
        p.string = text
        body.append(p)
    return str(soup)


def export_epub(db: Database, project_id: int, source_path: str | Path, out_path: str | Path) -> Path:
    source_path = Path(source_path)
    out_path = Path(out_path)
    book = epub.read_epub(str(source_path))

    chapters = db.conn.execute(
        "SELECT * FROM chapters WHERE project_id=? ORDER BY order_index", (project_id,)
    ).fetchall()

    # map chapter html -> translated paragraphs
    matched_items = []
    for ch in chapters:
        segs = db.segments_for_chapter(ch["id"])
        translated = [s["translated_text"] or s["source_text"] for s in segs]
        new_html = _replace_paragraphs(ch["html"] or "", translated)
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            if isinstance(item, epub.EpubNav) or "nav" in getattr(item, "properties", []):
                continue
            try:
                content = item.get_content().decode("utf-8", errors="replace")
            except Exception:
                continue
            if content.strip() == (ch["html"] or "").strip():
                item.set_content(new_html.encode("utf-8"))
                if ch["title"]:
                    item.title = ch["title"]
                matched_items.append(item)
                break

    # rebuild toc so NCX/nav titles are not None (ebooklib quirk after read_epub)
    if matched_items:
        book.toc = tuple(matched_items)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(out_path), book)
    return out_path
