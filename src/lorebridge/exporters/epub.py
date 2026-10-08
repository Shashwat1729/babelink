"""Build a translated EPUB (spec §68).

Chapters that came from an EPUB document are written back into that document (tags, classes,
images and CSS survive). Chapters without an original document (TXT / pasted / re-chunked
Gutenberg dumps) become fresh XHTML files. If nothing matches the source book, a clean book is built.
"""
from __future__ import annotations

import html as _html
from pathlib import Path
from typing import Optional

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

from ..db import Database
from ..segment import _HEADING_TAGS, segment_tags


def _replace_text_nodes(tag, new_text: str) -> None:
    """Replace a paragraph's text. Preserves the tag + attributes (class/style = alignment/spacing).
    Inline b/i/em are not preserved: segments are whole paragraphs (ceiling noted in spec §68)."""
    tag.clear()
    tag.append(new_text)


def _set_title(soup, title: Optional[str]) -> None:
    head = soup.find(_HEADING_TAGS)
    if title and head is not None:
        _replace_text_nodes(head, title)


def _replace_paragraphs(html: str, translated: list[str], sources: Optional[list[str]] = None, title: Optional[str] = None,
                        lang: Optional[str] = None) -> str:
    soup = BeautifulSoup(html, "lxml")
    root = soup.find("html")
    if root is not None and lang:  # the page declares the language it is now written in (hyphenation, fonts, screen readers)
        for attr in ("lang", "xml:lang"):
            if root.has_attr(attr):
                root[attr] = lang
    tags = segment_tags(soup)
    if len(tags) == len(translated):
        for tag, text in zip(tags, translated):
            _replace_text_nodes(tag, text)
        if sources:  # bilingual: original line after each translated one
            for tag, src in zip(tags, sources):
                orig = soup.new_tag("p", attrs={"class": "source-text", "lang": "und"})
                orig.string = src
                tag.insert_after(orig)
    else:  # structure differs from the segmentation: keep headings, rebuild the paragraphs
        body = soup.find("body") or soup
        heads = [h.extract() for h in soup.find_all(_HEADING_TAGS)]
        body.clear()
        for h in heads:
            body.append(h)
        for i, text in enumerate(translated):
            p = soup.new_tag("p")
            p.string = text
            body.append(p)
            if sources:
                orig = soup.new_tag("p", attrs={"class": "source-text"})
                orig.string = sources[i]
                body.append(orig)
    _set_title(soup, title)
    return str(soup)


def _chapter_texts(db: Database, ch):
    segs = db.segments_for_chapter(ch["id"])
    return [s["translated_text"] or s["source_text"] for s in segs], [s["source_text"] for s in segs]


def _fresh_html(title: str, translated: list[str], sources: Optional[list[str]], lang: str) -> str:
    body = "".join(
        f"<p>{_html.escape(t)}</p>" + (f'<p class="source-text">{_html.escape(sources[i])}</p>' if sources else "")
        for i, t in enumerate(translated)
    )
    return f"<h1>{_html.escape(title)}</h1>{body}"


def export_epub(db: Database, project_id: int, source_path: Optional[str | Path], out_path: str | Path, *,
                bilingual: bool = False, title: Optional[str] = None) -> Path:
    out_path = Path(out_path)
    project = db.get_project(project_id)
    lang = project["target_lang"]
    chapters = db.chapters(project_id)

    book = None
    originals: dict[str, object] = {}
    if source_path and Path(source_path).exists() and Path(source_path).suffix.lower() == ".epub":
        book = epub.read_epub(str(source_path))
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            if isinstance(item, epub.EpubNav) or "nav" in getattr(item, "properties", []):
                continue
            try:
                originals[item.get_content().decode("utf-8", errors="replace").strip()] = item
            except Exception:
                continue

    in_place = {ch["id"]: originals.get((ch["html"] or "").strip()) for ch in chapters}
    use_source_book = book is not None and any(in_place.values())
    if use_source_book:  # the book's own title/language metadata still describe the source text
        dc = book.metadata.setdefault(epub.NAMESPACES["DC"], {})
        dc["title"] = [(title or project["name"], {})]
        dc["language"] = [(lang, {})]
    else:
        book = epub.EpubBook()
        book.set_identifier(f"lorebridge-{project_id}")
        book.set_title(title or project["name"])
        book.set_language(lang)

    items = []
    for n, ch in enumerate(chapters, 1):
        translated, sources = _chapter_texts(db, ch)
        ch_title = ch["title_translated"] or ch["title"]
        src_lines = sources if bilingual else None
        item = in_place.get(ch["id"])
        if item is not None:
            item.set_content(_replace_paragraphs(ch["html"], translated, src_lines, ch["title_translated"], lang).encode("utf-8"))
        else:
            item = epub.EpubHtml(title=ch_title, file_name=f"chapter_{n:04d}.xhtml", lang=lang)
            item.content = _fresh_html(ch_title, translated, src_lines, lang)
            book.add_item(item)
        item.title = ch_title
        items.append(item)

    book.toc = tuple(items)
    if use_source_book:
        # keep the original spine (cover etc.); append chapters that had no document
        spine_ids = {x[0] if isinstance(x, tuple) else getattr(x, "id", x) for x in book.spine}
        book.spine = list(book.spine) + [i for i in items if i.id not in spine_ids]
    else:
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ["nav", *items]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(out_path), book)
    return out_path
