"""QA checks, human review, exporters/validation, metrics and corpus-eval helpers."""
import io
import zipfile

from ebooklib import epub

from lorebridge import service
from lorebridge.dataset_manifest import MANIFEST, check_manifest
from lorebridge.eval import chrf, term_adherence
from lorebridge.eval_real import align_reference, chapter_number
from lorebridge.exporters import export_epub, export_txt
from lorebridge.exporters.validate import validate_epub, validate_project
from lorebridge.pipeline import translate_project
from lorebridge.providers.base import MockProvider
from lorebridge.qa import check_segment, run_qa
from lorebridge.review import edit_segment
from lorebridge.story_bible import project_root

CH = "第一章 开始\n\n林凡走进青云宗，花了三天。\n\n大师兄点了点头。"


def kinds(findings):
    return {(sev, typ) for sev, typ, _ in findings}


def project(tmp_path, text=CH):
    db = service.open_workspace(tmp_path)
    pid = service.create_project(db, "novel", "zh", "en")
    service.add_pasted_chapter(db, tmp_path, pid, text, "Ch")
    return db, pid, project_root(tmp_path, pid)


# ---- QA -----------------------------------------------------------------------------------------
def test_check_segment_findings():
    assert ("error", "missing_translation") in kinds(check_segment("林凡来了。", "", "zh"))
    assert ("error", "prompt_leak") in kinds(check_segment("林凡来了。", "Translation: Lin Fan came. Here is the translation", "zh"))
    assert ("warning", "untranslated_source") in kinds(check_segment("林凡来了。", "林凡来了。", "zh"))
    assert ("warning", "number_mismatch") in kinds(check_segment("他等了3天。", "He waited for days.", "zh"))
    assert ("warning", "term_not_applied") in kinds(check_segment("林凡来了。", "Fan Lin came.", "zh", {"林凡": "Lin Fan"}))
    assert not check_segment("林凡来了。", "Lin Fan came.", "zh", {"林凡": "Lin Fan"})


def test_run_qa_moves_states_and_never_touches_human_approved(tmp_path):
    db, pid, root = project(tmp_path)
    translate_project(db, pid, MockProvider(), project_dir=root)
    seg = db.segments_for_chapter(db.chapters(pid)[0]["id"])
    db.update_segment(seg[0]["id"], translated="", state=None)
    db.conn.execute("UPDATE segments SET translated_text='' WHERE id=?", (seg[0]["id"],))
    db.conn.commit()
    edit_segment(db, pid, seg[1]["id"], "Lin Fan walked into the Qingyun Sect in three days.")
    stats = run_qa(db, pid)
    states = {s["id"]: s["state"] for s in db.segments_for_chapter(db.chapters(pid)[0]["id"])}
    assert states[seg[0]["id"]] == "NEEDS_REVIEW"
    assert states[seg[1]["id"]] == "HUMAN_APPROVED"
    assert stats["needs_review"] == 1
    run_qa(db, pid)  # idempotent: findings are replaced, not duplicated
    assert len([q for q in db.qa_for_project(pid) if q["segment_id"] == seg[0]["id"] and q["type"] == "missing_translation"]) == 1


# ---- human review -------------------------------------------------------------------------------
def test_edit_scope_project_feeds_translation_memory(tmp_path):
    db, pid, root = project(tmp_path)
    translate_project(db, pid, MockProvider(), project_dir=root)
    seg = db.segments_for_chapter(db.chapters(pid)[0]["id"])[1]  # "第一章 开始" became the chapter title
    edit_segment(db, pid, seg["id"], "The Senior Brother nodded.", scope="project")
    assert db.tm_lookup(pid, seg["source_text"])["translated_text"] == "The Senior Brother nodded."
    assert db.get_segment(seg["id"])["state"] == "HUMAN_APPROVED"
    assert db.edits_for_segment(seg["id"])[0]["scope"] == "project"
    mock = MockProvider()
    translate_project(db, pid, mock, project_dir=root)  # approved text survives later runs
    assert db.get_segment(seg["id"])["translated_text"] == "The Senior Brother nodded."


# ---- export ---------------------------------------------------------------------------------------
def make_source_epub(path):
    book = epub.EpubBook()
    book.set_identifier("x"); book.set_title("Novel"); book.set_language("zh")
    c1 = epub.EpubHtml(title="c1", file_name="c1.xhtml", lang="zh")
    c1.content = '<html><body><h1>第一章 开始</h1><p class="a">林凡走进青云宗，花了三天。</p><p>大师兄点了点头。</p></body></html>'
    book.add_item(c1)
    book.toc = (c1,)
    book.spine = ["nav", c1]
    book.add_item(epub.EpubNcx()); book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)


def test_export_in_place_fresh_and_bilingual(tmp_path):
    src = tmp_path / "novel.epub"
    make_source_epub(src)
    db = service.open_workspace(tmp_path / "ws")
    pid = service.create_project(db, "novel", "zh", "en")
    service.import_file(db, pid, src)
    root = project_root(tmp_path / "ws", pid)
    translate_project(db, pid, MockProvider(), project_dir=root)
    for s in db.conn.execute("SELECT id, source_text FROM segments").fetchall():
        edit_segment(db, pid, s["id"], "Translated: " + str(s["id"]))

    inplace = export_epub(db, pid, src, tmp_path / "inplace.epub")
    fresh = export_epub(db, pid, None, tmp_path / "fresh.epub")
    bi = export_epub(db, pid, None, tmp_path / "bi.epub", bilingual=True)
    for out in (inplace, fresh):  # bilingual keeps the source on purpose, so it is not "untranslated"
        assert validate_epub(out)["ok"], validate_epub(out)
    meta = epub.read_epub(str(inplace))
    assert meta.get_metadata("DC", "title")[0][0] == "novel" and meta.get_metadata("DC", "language")[0][0] == "en"  # not the source book's
    html = "".join(zipfile.ZipFile(inplace).read(n).decode("utf-8") for n in zipfile.ZipFile(inplace).namelist() if n.endswith("c1.xhtml"))
    assert 'class="a"' in html and "Translated:" in html and "林凡走进" not in html  # attributes kept, source replaced
    assert "林凡走进" in "".join(zipfile.ZipFile(bi).read(n).decode("utf-8") for n in zipfile.ZipFile(bi).namelist() if n.endswith("xhtml"))

    txt = export_txt(db, pid, tmp_path / "o.txt")
    assert "Translated:" in txt.read_text(encoding="utf-8")
    assert validate_project(db, pid)["ok"]


def test_validate_flags_unfinished_and_untranslated_epub(tmp_path):
    db, pid, _ = project(tmp_path)
    report = validate_project(db, pid)
    assert not report["ok"] and report["untranslated"] == 2
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("c.xhtml", "<html><body><p>" + "林凡走进青云宗。" * 20 + "</p></body></html>")
    bad = tmp_path / "bad.epub"
    bad.write_bytes(buf.getvalue())
    assert not validate_epub(bad)["ok"]
    assert not validate_epub(tmp_path / "missing.epub")["ok"]


# ---- metrics / corpus helpers -------------------------------------------------------------------
def test_chrf_and_term_adherence():
    ref = "Lin Fan walked into the Qingyun Sect and bowed to the Senior Brother."
    assert chrf(ref, ref) == 100.0
    assert chrf("", ref) == 0.0
    assert chrf("Lin Fan walked into the sect.", ref) > chrf("The weather was fine and nobody came.", ref) > 0
    assert term_adherence("林凡来了，大师兄也来了。", "Lin Fan came, so did the Senior Brother.", {"林凡": "Lin Fan", "大师兄": "Senior Brother", "师姐": "Senior Sister"}) == (2, 2)
    assert term_adherence("林凡来了", "Fan Lin came", {"林凡": "Lin Fan"}) == (0, 1)


def test_chapter_number_and_alignment():
    assert chapter_number("第三回 贾雨村夤缘复旧职") == 3
    assert chapter_number("第十二章 试炼") == 12
    assert chapter_number("第一百零五回") == 105
    assert chapter_number("CHAPTER IV.") == 4 and chapter_number("Chapter 12") == 12
    assert chapter_number("Preface") is None
    en = ["a" * 100] * 10
    assert len(align_reference([10] * 10, 5, en).split("\n")) == 5
    assert align_reference([10] * 10, 10, en).count("\n") == 9


def test_dataset_manifest_complete():
    assert check_manifest() == []
    assert {"gutenberg-24264", "gutenberg-9603"} <= {d["id"] for d in MANIFEST}


# ---- providers ------------------------------------------------------------------------------------
def test_ollama_disables_thinking_and_retries_without_it_on_old_servers(monkeypatch):
    import httpx

    from lorebridge.providers.ollama import OllamaProvider

    bodies = []

    def fake_post(url, json, timeout):
        bodies.append(dict(json))
        if "think" in json and len(bodies) == 1:
            return httpx.Response(400, text='{"error":"model does not support think"}', request=httpx.Request("POST", url))
        return httpx.Response(200, json={"message": {"content": "ok"}, "eval_count": 3}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    p = OllamaProvider("qwen3:4b")
    assert p._chat_once("sys", "user", 0.2, 50) == "ok"
    assert bodies[0]["think"] is False and "think" not in bodies[1]
    assert p.usage["completion_tokens"] == 3


def test_term_suggestions_keep_names_and_drop_common_words(tmp_path):
    """Held-out chapter regression: 废物->'waste', 身体->'body', 一名->'a' were enforced and rewrote normal prose."""
    from lorebridge.terminology import build_glossary, suggest_term_translations, upsert_candidates

    class Terms(MockProvider):
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            ren = {"罗天": "Luo Tian", "青昌城": "Qingchang City", "废物": "waste", "身体": "body", "一名": "a"}
            return [{"term": t, "type": "character", "translation": ren[t]} for t, _ in items]

    db, pid, _ = project(tmp_path, "罗天来到青昌城。\n\n一名废物，身体很弱。")
    upsert_candidates(db, pid, [(t, 3) for t in ("罗天", "青昌城", "废物", "身体", "一名")])
    assert suggest_term_translations(db, pid, Terms()) == 2
    assert build_glossary(db, pid) == {"罗天": "Luo Tian", "青昌城": "Qingchang City"}
    assert {t["status"] for t in db.terminology_for_project(pid) if t["source_forms"] in ('["废物"]', '["一名"]')} == {"ignored"}
