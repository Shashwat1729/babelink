from pathlib import Path

import pytest
from ebooklib import epub

from storytranslate.db import Database
from storytranslate.exporters.epub import export_epub
from storytranslate.importers import import_epub, import_txt
from storytranslate.pipeline import analyze_project, translate_project
from storytranslate.providers.base import MockProvider
from storytranslate.segment import chapters_from_text
from storytranslate.terminology import mine_terms


def make_epub(path: Path) -> None:
    book = epub.EpubBook()
    book.set_identifier("test")
    book.set_title("Test Novel")
    book.set_language("zh")
    c1 = epub.EpubHtml(title="Chapter 1", file_name="c1.xhtml", lang="zh")
    c1.content = "<html><body><h1>第一章 开始</h1><p>林凡修炼到了炼气期。</p><p>他感觉到了异样的气息。</p></body></html>"
    c2 = epub.EpubHtml(title="Chapter 2", file_name="c2.xhtml", lang="zh")
    c2.content = "<html><body><h1>第二章 突破</h1><p>林凡进入了筑基期。</p></body></html>"
    book.add_item(c1)
    book.add_item(c2)
    book.toc = (c1, c2)
    book.spine = ["nav", c1, c2]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)


def test_chapters_from_text():
    text = "第一章 开始\n\n你好世界。\n\n第二章 突破\n\n他突破了。"
    chapters = chapters_from_text(text)
    assert len(chapters) == 2
    assert chapters[0].paragraphs


def test_mine_terms():
    texts = ["炼气期修炼炼气期。" * 3, "筑基期筑基期筑基期。" * 3]
    terms = mine_terms(texts, min_count=3)
    flat = [t for t, _ in terms]
    assert any("炼气" in t for t in flat)


def test_end_to_end_epub(tmp_path: Path):
    src = tmp_path / "novel.epub"
    make_epub(src)
    db = Database(tmp_path / "project.db")
    pid = db.create_project("test", target_lang="en")
    import_epub(db, pid, src)
    info = analyze_project(db, pid)
    assert info["language"] == "zh"
    assert info["segments"] == 3
    n = translate_project(db, pid, MockProvider())
    assert n == 3
    # states
    states = {r["state"] for r in db.conn.execute("SELECT state FROM segments").fetchall()}
    assert states == {"TRANSLATED"}
    out = tmp_path / "out.epub"
    export_epub(db, pid, src, out)
    assert out.exists()
    db.close()


def test_txt_import_and_resume_state(tmp_path: Path):
    p = tmp_path / "novel.txt"
    p.write_text("Chapter 1\n\nHello world.\n\nChapter 2\n\nSecond part.", encoding="utf-8")
    db = Database(tmp_path / "p.db")
    pid = db.create_project("t")
    import_txt(db, pid, p)
    analyze_project(db, pid)
    n1 = translate_project(db, pid, MockProvider(), limit=1)
    assert n1 == 1
    # resume: only remaining segments get translated
    n2 = translate_project(db, pid, MockProvider())
    assert n2 == 1
    assert len(db.open_segments(pid)) == 0
    db.close()
