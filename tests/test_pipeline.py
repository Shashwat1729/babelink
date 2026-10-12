from pathlib import Path

import pytest
from ebooklib import epub

from lorebridge.db import Database
from lorebridge.exporters.epub import export_epub
from lorebridge.importers import import_epub, import_txt
from lorebridge.importers.base import store_chapters
from lorebridge.pipeline import analyze_project, translate_project
from lorebridge.providers.base import MockProvider
from lorebridge.segment import ParsedChapter, chapters_from_text, to_html
from lorebridge.terminology import mine_terms


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


def test_wrapped_paragraph_is_not_taken_as_chapter_subtitle():
    wrapped = "CHAPTER I.\n\nThe quick brown fox jumps over\nthe lazy dog and keeps\nrunning for a long time.\n\nSecond paragraph."
    (ch,) = chapters_from_text(wrapped)
    assert ch.title == "CHAPTER I." and ch.paragraphs[0].startswith("The quick brown fox jumps over the lazy dog")
    (ch,) = chapters_from_text("CHAPTER II.\n\nTHE PERIOD\n\nIt was a long time ago.")  # a real subtitle line still joins the title
    assert ch.title == "CHAPTER II. THE PERIOD" and ch.paragraphs == ["It was a long time ago."]


def test_translate_auto_suggests_when_glossary_empty(tmp_path):
    """Upload-and-go: translate classifies terms itself when nobody suggested yet."""
    from lorebridge.terminology import build_glossary, upsert_candidates

    class Terms(MockProvider):
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            return [{"term": t, "type": "character", "translation": "Luo Tian"} for t, _ in items]

    db = Database(tmp_path / "auto.db")
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    doc = db.add_document(pid, "x", "eval", "x")
    paras = ["羅天來了。", "羅天去了。", "羅天又來了。"]
    cids = store_chapters(db, pid, doc, [ParsedChapter("c", to_html(paras), paras)])
    upsert_candidates(db, pid, [("羅天", 3)])
    assert build_glossary(db, pid) == {}
    assert translate_project(db, pid, Terms(), chapter_id=cids[0]) == 3
    assert build_glossary(db, pid) == {"羅天": "Luo Tian"}
    assert "Luo Tian" in db.get_segment(db.segments_for_chapter(cids[0])[0]["id"])["translated_text"]
    db.close()


def test_placeholders_restore_renderings_structurally(tmp_path):
    """E09 pipeline: with the placeholders feature the MT model sees proxies
    (MockProvider echoes them back), renderings are restored post-draw, and
    the stored output carries the name even though no prompt hint was sent."""
    from lorebridge.models import TerminologyEntry
    from lorebridge.terminology import build_glossary

    db = Database(tmp_path / "ph.db")
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    doc = db.add_document(pid, "x", "eval", "x")
    paras = ["黛玉去見賈政。", "賈政問黛玉。"]
    cids = store_chapters(db, pid, doc, [ParsedChapter("c", to_html(paras), paras)])
    db.add_terminology(TerminologyEntry(None, pid, ["黛玉"], {"en": "Dai Yu"}, "character", "preferred", "confirmed", 2))
    db.add_terminology(TerminologyEntry(None, pid, ["賈政"], {"en": "Jia Zheng"}, "character", "preferred", "confirmed", 2))
    assert build_glossary(db, pid) == {"黛玉": "Dai Yu", "賈政": "Jia Zheng"}
    assert translate_project(db, pid, MockProvider(), chapter_id=cids[0],
                             features=frozenset({"placeholders"})) == 2
    texts = [s["translated_text"] for s in db.segments_for_chapter(cids[0])]
    assert "Dai Yu" in texts[0] and "Jia Zheng" in texts[0] and "Dai Yu" in texts[1]
    assert "\u27e6E" not in texts[0] and "\u27e6E" not in texts[1]  # no proxy leaks


def test_split_long_pieces_and_rejoin(tmp_path):
    """E13 unit: short text passes through as one piece; long text splits on
    sentence boundaries and rejoins (MockProvider echoes source)."""
    from lorebridge.segment import split_long

    assert split_long("黛玉去了。", 300) == ["黛玉去了。"]
    long = "黛玉去了。" + "賈政問話。" * 200
    pieces = split_long(long, 300)
    assert len(pieces) > 1 and "".join(pieces) == long
    assert all(p.endswith("。") for p in pieces)


def test_translate_split_pieces_rejoins_one_segment(tmp_path):
    """E13 pipeline: split_pieces=N translates sentence pieces with glossary
    from the full segment, stores one rejoined segment (MockProvider echo)."""
    db = Database(tmp_path / "sp.db")
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    doc = db.add_document(pid, "x", "eval", "x")
    paras = ["黛玉去了。" + "賈政問話。" * 100]
    cids = store_chapters(db, pid, doc, [ParsedChapter("c", to_html(paras), paras)])
    assert translate_project(db, pid, MockProvider(), chapter_id=cids[0], split_pieces=300) == 1
    texts = [s["translated_text"] for s in db.segments_for_chapter(cids[0])]
    assert texts[0] == paras[0]  # echo pieces rejoined losslessly
    assert texts[0].count("賈政問話") == 100
    db.close()
    db.close()


def test_translate_skips_suggest_with_mt_only_translator(tmp_path):
    """An MT-only translator alone must not classify (it cannot); glossary stays empty."""
    from lorebridge.providers.hymt_ollama import HymtOllamaProvider
    from lorebridge.terminology import build_glossary, upsert_candidates

    db = Database(tmp_path / "mt.db")
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    doc = db.add_document(pid, "x", "eval", "x")
    paras = ["羅天來了。", "羅天去了。", "羅天又來了。"]
    cids = store_chapters(db, pid, doc, [ParsedChapter("c", to_html(paras), paras)])
    upsert_candidates(db, pid, [("羅天", 3)])
    p = HymtOllamaProvider.__new__(HymtOllamaProvider)  # no network: translate never reached
    p.calls = []
    try:
        translate_project(db, pid, p, chapter_id=cids[0])
    except AttributeError:
        pass  # translate itself needs the network; the point is suggest did not run
    assert build_glossary(db, pid) == {}
    db.close()


def test_retarget_inherits_story_graph_without_renderings(tmp_path):
    """A second target language starts from the same source: term forms and
    concepts carry over, renderings are re-done; translations/TM stay behind."""
    from lorebridge import service
    from lorebridge.terminology import build_glossary, upsert_candidates

    db = Database(tmp_path / "r.db")
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    doc = db.add_document(pid, "x", "eval", "x")
    paras = ["羅天來了。", "羅天去了。"]
    cids = store_chapters(db, pid, doc, [ParsedChapter("c", to_html(paras), paras)])
    upsert_candidates(db, pid, [("羅天", 2)])
    from lorebridge.models import TerminologyEntry
    db.add_terminology(TerminologyEntry(None, pid, ["羅天"], {"en": "Luo Tian"}, "character", "preferred", "confirmed", 2))
    npid = service.retarget(db, pid, "ja", "t-ja")
    assert db.get_project(npid)["target_lang"] == "ja"
    assert [s["source_text"] for s in db.segments_for_chapter(db.chapters(npid)[0]["id"])] == paras
    assert all(s["translated_text"] is None for s in db.conn.execute(
        "SELECT translated_text FROM segments WHERE project_id=?", (npid,)).fetchall())
    inherited = db.terminology_for_project(npid)
    assert len(inherited) == 1 and inherited[0]["concept"] == "character"
    assert inherited[0]["status"] == "suggested" and build_glossary(db, npid) == {}
    db.close()
    (ch,) = chapters_from_text("第三回\n贾雨村夤缘复旧职\n\n却说黛玉。")
    assert ch.title == "第三回 贾雨村夤缘复旧职"
