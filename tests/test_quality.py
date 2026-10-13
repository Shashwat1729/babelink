"""QA checks, human review, exporters/validation, metrics and corpus-eval helpers."""
import io
import json
import zipfile

from ebooklib import epub

from babelink import service
from babelink.dataset_manifest import MANIFEST, check_manifest
from babelink.db import Database
from babelink.eval import chrf, term_adherence
from babelink.eval_real import align_reference, chapter_number
from babelink.exporters import export_epub, export_txt
from babelink.exporters.validate import validate_epub, validate_project
from babelink.pipeline import translate_project
from babelink.providers.base import MockProvider
from babelink.qa import check_segment, run_qa
from babelink.review import edit_segment
from babelink.story_bible import project_root

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
    assert ("warning", "untranslated_source") in kinds(check_segment("ACK!!!", "ACK！！！", "en"))
    assert ("warning", "untranslated_source") not in kinds(check_segment("2024", "2024", "en"))
    assert ("warning", "untranslated_source") not in kinds(check_segment("你好。", "Hello, how are you?", "zh"))
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

    from babelink.providers.ollama import OllamaProvider

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
    """Held-out chapter regression: 废物->'waste', 身体->'body', 一名->'a' were enforced and rewrote normal prose.
    E17: no word lists — the prompt contract (lowercase rendering + non-name
    concept = ordinary word) keeps them as vote candidates, never enforced."""
    from babelink.terminology import build_glossary, suggest_term_translations, upsert_candidates

    class Terms(MockProvider):
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            ren = {"罗天": ("character", "Luo Tian"), "青昌城": ("location", "Qingchang City"),
                   "废物": ("other", "waste"), "身体": ("other", "body"), "一名": ("other", "a")}
            return [{"term": t, "type": ren[t][0], "translation": ren[t][1]} for t, _ in items]

    db, pid, _ = project(tmp_path, "罗天来到青昌城。\n\n一名废物，身体很弱。")
    upsert_candidates(db, pid, [(t, 3) for t in ("罗天", "青昌城", "废物", "身体", "一名")])
    assert suggest_term_translations(db, pid, Terms()) == 2
    assert build_glossary(db, pid) == {"罗天": "Luo Tian", "青昌城": "Qingchang City"}
    assert {t["status"] for t in db.terminology_for_project(pid) if t["source_forms"] in ('["废物"]', '["一名"]')} == {"ignored"}


def test_term_suggestions_drop_echo_translations(tmp_path):
    """ParallelFiction regression: helper echoed 陛下->陛下 / 反乱->反乱 as
    'suggested' translations; the model then ignored the bogus mapping and
    term adherence scored 0/2."""
    from babelink.terminology import build_glossary, suggest_term_translations, upsert_candidates

    class Echo(MockProvider):
        def suggest_terms(self, items, source_lang="ja", target_lang="en"):
            return [{"term": t, "type": "title", "translation": t} for t, _ in items]

    db, pid, _ = project(tmp_path, "陛下万岁。\n\n反乱が起きた。")
    upsert_candidates(db, pid, [("陛下", 4), ("反乱", 3)])
    assert suggest_term_translations(db, pid, Echo()) == 0
    assert build_glossary(db, pid) == {}
    assert {t["status"] for t in db.terminology_for_project(pid)} == {"ignored"}


def test_mining_junk_never_reaches_the_helper():
    """E17: only STRUCTURAL shapes filter before the helper (number+counter,
    saying verbs). Which ordinary words are not terms is the helper's verdict
    + dispersion rank — never a word list."""
    from babelink.terminology import _is_mining_junk, mine_terms

    for junk in ("三日", "二人", "笑曰", "答曰", "3日"):
        assert _is_mining_junk(junk), junk
    for keep in ("異史氏曰", "孔子曰", "林凡", "黛玉", "今日", "起來"):  # name + 曰 stays classifiable;
        assert not _is_mining_junk(keep), keep  # ordinary words pass to the helper, which drops them
    terms = dict(mine_terms(["三日後，笑曰今日去。\n\n林凡來了三日，笑曰明日去。\n\n三日又過，林凡又來。"] * 3))
    assert "三日" not in terms and "笑曰" not in terms
    assert "林凡" in terms


def test_mining_junk_red_chamber_budget_eaters():
    """E17 replaces the old stop-list: BURSTINESS ranks terms, not raw count.
    Grammar (的: every passage) is dropped by dispersion; bursty names (黛玉:
    4 passages) outrank diffuse function words (起來/姐姐: spread thin) — in
    any language, with no word lists."""
    from babelink.terminology import mine_terms

    core = ["黛玉黛玉去見王夫人，賈政問如海。",
            "如海笑，眾人見如海，王夫人同如海說話，黛玉黛玉笑。",
            "黛玉黛玉方進入房，賈政點頭。",
            "王夫人進來說話，黛玉黛玉聽見了。"]
    filler = ["他起來了，姐姐又過去。", "如今聽見了，妹妹說話。",
              "心中打發明白，身上女兒聽說。", "進來回去過去，婆子孩子。",
              "他回來了，哥哥又進去。", "原來如此，弟弟告訴。",
              "家裡外頭，嫂子媳婦。", "明兒今兒，丫頭小廝。"]
    texts = core + filler  # 12 passages: 的 everywhere, names bursty, function words diffuse
    ranked = [t for t, _ in mine_terms(texts, min_count=2, top=20)]
    assert "黛玉" in ranked and "王夫人" in ranked and "賈政" in ranked and "如海" in ranked
    assert "的" not in ranked  # dispersion: present in every passage
    assert "姐姐" not in ranked  # diffuse singleton: never surfaces, helper budget spared


def test_mine_terms_finds_katakana_names():
    from babelink.terminology import mine_terms

    terms = dict(mine_terms(["アルメリアの反乱はヘンリーによって鎮圧された。"] * 3))
    assert "アルメリア" in terms and "ヘンリー" in terms
    from babelink.terminology import mine_terms

    terms = dict(mine_terms(["アルメリアの反乱はヘンリーによって鎮圧された。"] * 3))
    assert "アルメリア" in terms and "ヘンリー" in terms


def test_pinyin_shaped_renderings_enforced_only_for_names():
    from babelink.terminology import _is_bad_translation, _is_pinyin_shaped

    assert _is_pinyin_shaped("San Ri") and _is_pinyin_shaped("Yuzhen") and _is_pinyin_shaped("Ruci")
    assert _is_pinyin_shaped("Wuhé")  # tone marks still read as pinyin
    assert _is_pinyin_shaped("Yishi Shi Yu") and _is_pinyin_shaped("Lin Fan")
    for good in ("Prince", "Madam", "Tell", "Day", "Lady", "Sister", "Azure Cloud Sect", "Old Lady"):
        assert not _is_pinyin_shaped(good), good
    # transliteration of a non-name is junk …
    assert _is_bad_translation("三日", "San Ri", "en", "other")
    assert _is_bad_translation("如此", "Ruci", "en", "other")
    # … but a real name keeps its pinyin, as do ordinary good renderings
    assert not _is_bad_translation("雨村", "Yucun", "en", "character")
    assert _is_bad_translation("雨村", "Yuzhen", "en", "character")  # 村=cun: authority overrules habit
    assert _is_bad_translation("薛姨媽", "Xie Yimai", "en", "character")  # 媽=ma, not mai: phonetics, not lists
    assert not _is_bad_translation("黛玉", "Dai Yu", "en", "character")
    assert not _is_bad_translation("老太太", "Old Lady", "en", "title")
    # E17: ordinary-word renderings ("Next Day", "Small Boy", "Brother") are
    # NOT gate-rejected by word lists anymore — translator agreement decides
    # (see test_agreement_gate_demotes_unshared_renderings); the structural
    # survivors stay rejected:
    assert _is_bad_translation("且說", "AND SAY", "en", "other")  # ALL-CAPS
    assert _is_bad_translation("陛下", "陛下", "en", "title", "ja")  # echo
    assert not _is_bad_translation("次日", "Next Day", "en", "other")  # agreement's job now
    assert not _is_bad_translation("小廝", "Small Boy", "en", "character")
    assert not _is_bad_translation("兄弟", "Brother", "en", "character")
    # pass-through transliterations the phonetics accept ("Xie Yima": ma ok)
    # are agreement's job too — covered live in the gate test below


def test_agreement_gate_demotes_unshared_renderings(tmp_path):
    """E17's replacement for the deleted word lists: helper proposes Small
    Boy / Brother / Xie Yimai as characters; the translator's own habit
    (servant boy / brothers / Aunt Xue) disagrees, so they become vote
    candidates — never enforced. An agreed name (Jia Zheng) enforces;
    probe outage fails open (helper's verdict stands)."""
    import json

    from babelink.terminology import build_glossary, suggest_term_translations, upsert_candidates

    class Helper(MockProvider):
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            ren = {"小廝": "Small Boy", "兄弟": "Brother", "薛姨媽": "Xie Yimai",
                   "賈政": "Jia Zheng", "黛玉": "Dai Yu"}
            return [{"term": t, "type": "character", "translation": ren[t], "enforce": True} for t, _ in items]

    class MT(MockProvider):
        def translate(self, source, **kw):
            return {"小廝": "a servant boy", "兄弟": "his brothers", "薛姨媽": "Aunt Xue",
                    "賈政": "Jia Zheng", "黛玉": "Daiyu"}[source]

    class Down(MockProvider):
        def translate(self, source, **kw):
            from babelink.prompts import ProviderError
            raise ProviderError("down")

    db, pid, _ = project(tmp_path, "小廝來了。\n\n兄弟笑了。\n\n薛姨媽到了。\n\n賈政問黛玉。")
    upsert_candidates(db, pid, [(t, 3) for t in ("小廝", "兄弟", "薛姨媽", "賈政", "黛玉")])
    # 黛玉=Dai Yu is a CORRECT reading (dai+yu): probe agrees, authority
    # passes, enforces. (The misread twin Da Yu is covered one test up.)
    assert suggest_term_translations(db, pid, Helper(), translator=MT()) == 2
    assert build_glossary(db, pid) == {"賈政": "Jia Zheng", "黛玉": "Dai Yu"}
    stored = {json.loads(t["source_forms"])[0]: t for t in db.terminology_for_project(pid)}
    assert sorted(json.loads(stored["小廝"]["canonical_translation"]).get("candidates", [])) == ["A Servant Boy", "Small Boy"]
    # outage: helper's verdict stands, nothing demoted by silence
    db2, pid2, _ = project(tmp_path, "小廝來了。\n\n賈政問。")
    upsert_candidates(db2, pid2, [("小廝", 3), ("賈政", 3)])
    assert suggest_term_translations(db2, pid2, Helper(), translator=Down()) == 2


def test_lowercase_renderings_normalized_then_judged(tmp_path):
    """The terms prompt asks for natural casing: 'his majesty' must normalize
    to 'His Majesty' and enforce, while 'three days' normalizes and is dropped."""
    from babelink.terminology import build_glossary, suggest_term_translations, upsert_candidates

    class Natural(MockProvider):
        def suggest_terms(self, items, source_lang="ja", target_lang="en"):
            types = {"陛下": "title", "三日": "other"}
            return [{"term": t, "type": types[t], "translation": {"陛下": "his majesty", "三日": "three days"}[t]}
                    for t, _ in items]

    db, pid, _ = project(tmp_path, "陛下万歳。\n\n三日経った。\n\n陛下おわす。三日また経った。")
    upsert_candidates(db, pid, [("陛下", 3), ("三日", 3)])
    assert suggest_term_translations(db, pid, Natural()) == 1
    assert build_glossary(db, pid) == {"陛下": "His Majesty"}


def test_aliases_with_one_rendering_merge(tmp_path):
    from babelink.terminology import build_glossary, suggest_term_translations, upsert_candidates

    class Names(MockProvider):
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            ren = {"黛玉": "Dai Yu", "林黛玉": "Dai Yu"}
            return [{"term": t, "type": "character", "translation": ren[t]} for t, _ in items]

    db, pid, _ = project(tmp_path, "黛玉來了。\n\n林黛玉去了。\n\n黛玉又來了，林黛玉又去了。")
    upsert_candidates(db, pid, [("黛玉", 4), ("林黛玉", 3)])
    assert suggest_term_translations(db, pid, Names()) == 2
    assert build_glossary(db, pid) == {"黛玉": "Dai Yu", "林黛玉": "Dai Yu"}
    assert sum(1 for t in db.terminology_for_project(pid) if t["status"] != "ignored") == 1


def test_term_suggestions_drop_all_caps_renderings(tmp_path):
    """Dream of the Red Chamber run: 且說=AND SAY, 一般=GENERAL entered the
    glossary; no name renders in ALL CAPS."""
    from babelink.terminology import build_glossary, suggest_term_translations, upsert_candidates

    class Shouty(MockProvider):
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            return [{"term": t, "type": "other", "translation": "AND SAY"} for t, _ in items]

    db, pid, _ = project(tmp_path, "且說黛玉來了。\n\n且說寶玉去了。\n\n且說眾人散了。")
    upsert_candidates(db, pid, [("且說", 3)])
    assert suggest_term_translations(db, pid, Shouty()) == 0
    assert build_glossary(db, pid) == {}


def test_glossed_inscription_is_not_leftover(tmp_path):
    """Red Chamber ch3: the plaque “敕造宁国府” (Built by imperial order…)
    is a translated quotation, not untranslated source."""
    from babelink.qa import check_segment

    src = '匾上大書"敕造宁國府"五個大字'
    quoted = 'Above the gate hung a plaque bearing the five big characters “敕造宁国府” (Commissioned by the Emperor to build the Ningguo Mansion).'
    assert [t for _, t, _ in check_segment(src, quoted, "zh", {}) if t == "untranslated_source"] == []
    bare = "Above the gate hung 敕造宁国府 characters."
    assert any(t == "untranslated_source" for _, t, _ in check_segment(src, bare, "zh", {}))


def test_translator_cross_check_records_vote_candidates(tmp_path):
    """Red Chamber: helper's 黛玉=Da Yu is a phonetic misread (dai≠da) —
    never enforced; both surfaces stay as vote candidates and the book
    election picks the winner. An agreeing one (Jia Zheng) enforces;
    probe failures fail open."""
    import json

    from babelink.prompts import ProviderError
    from babelink.terminology import build_glossary, suggest_term_translations, upsert_candidates

    class Helper(MockProvider):
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            ren = {"黛玉": "Da Yu", "賈政": "Jia Zheng", "雨村": "Yu Jun", "如海": "Ru Hai"}
            return [{"term": t, "type": "character", "translation": ren[t], "enforce": True} for t, _ in items]

    class MT(MockProvider):
        def translate(self, source, **kw):
            return {"黛玉": "Daiyu", "賈政": "Jia Zheng", "雨村": "Yucun", "如海": "Ru Hai"}[source]

    db, pid, _ = project(tmp_path, "黛玉去見賈政。\n\n雨村問如海。\n\n黛玉笑了。")
    upsert_candidates(db, pid, [("黛玉", 4), ("賈政", 3), ("雨村", 3), ("如海", 3)])
    assert suggest_term_translations(db, pid, Helper(), translator=MT()) == 2
    assert build_glossary(db, pid) == {"賈政": "Jia Zheng", "如海": "Ru Hai"}
    stored = {json.loads(t["source_forms"])[0]: t for t in db.terminology_for_project(pid)}
    cands = json.loads(stored["黛玉"]["canonical_translation"]).get("candidates", [])
    assert cands == ["Da Yu", "Daiyu"], cands  # misread kept as loser: the election normalizes it out
    yu = json.loads(stored["雨村"]["canonical_translation"]).get("candidates", [])
    assert yu == ["Yu Jun", "Yucun"], yu  # both surfaces vote; the book picks

    class Down(MockProvider):
        def translate(self, source, **kw):
            raise ProviderError("down")

    # A dead probe fails the RUN open (no crash, valid terms still enforce)
    # without forcing misreads through: Da Yu stays rejected, Ru Hai enforces.
    db2, pid2, _ = project(tmp_path, "黛玉來了。\n\n如海去了。\n\n黛玉又來了。")
    upsert_candidates(db2, pid2, [("黛玉", 3), ("如海", 3)])
    assert suggest_term_translations(db2, pid2, Helper(), translator=Down()) == 1
    assert build_glossary(db2, pid2) == {"如海": "Ru Hai"}


def test_estimate_roundtrip_and_entity_recall():
    """E11 pure signals: empty back-translation scores 0; a name dropped in EN
    ('the girl' for 黛玉) shows full chrF-ish text but zero entity recall."""
    from babelink.estimate import entity_recall, roundtrip_chrf

    src = "黛玉去了，賈政問話。"
    gloss = {"黛玉": "Daiyu", "賈政": "Jia Zheng"}
    assert roundtrip_chrf(src, "") == 0.0
    assert roundtrip_chrf(src, src) == 100.0
    r, missing = entity_recall(src, "黛玉去了，賈政問話。", gloss)
    assert (r, missing) == (1.0, [])
    r2, missing2 = entity_recall(src, "女孩去了，有人問話。", gloss)
    assert r2 == 0.0 and set(missing2) == {"黛玉", "賈政"}
    assert entity_recall("無名之輩。", "anything", {}) == (1.0, [])


def test_qa_flags_unmapped_proxy_remnants():
    """DG18: ?E200?/[E4] remnants are never English — error (re-translate),
    while clean text and real bracket usage ('vitamin E4') stay clean."""
    from babelink.qa import check_segment

    types = [t for _, t, _ in check_segment("黛玉去了。", "When ?E200? arrived, [E4] saw.", "zh")]
    assert "proxy_remnant" in types
    assert all(s == "error" for s, t, _ in check_segment("黛玉去了。", "?E200?", "zh") if t == "proxy_remnant")
    clean = [t for _, t, _ in check_segment("黛玉去了。", "Daiyu took vitamin E4 daily.", "zh")]
    assert "proxy_remnant" not in clean


def test_qa_flags_truncated_ending():
    """Rashomon: output stopping mid-word ('religious ') at 1/5 of budget —
    length ratios miss it; missing terminal punctuation is the tell."""
    from babelink.qa import check_segment

    src = "羅生門下有乞丐。" * 10  # 60+ chars
    types = [t for _, t, _ in check_segment(src, "Under Rashomon there were beggars and religious ", "zh")]
    assert "truncated_ending" in types
    ok = [t for _, t, _ in check_segment(src, "Under Rashomon there were beggars.", "zh")]
    assert "truncated_ending" not in ok
    short = [t for _, t, _ in check_segment("去了。", "went", "zh")]
    assert "truncated_ending" not in short  # short sources exempt


def test_reconcile_prunes_terms_the_book_never_uses(tmp_path):
    """DG17: an enforced rendering no output uses ('Underground' — the book
    says 'below ground'/'down there') is not a term: ignored, so it stops
    eating helper budget, prompt tokens and proxies. A used rendering
    ('Treasure') survives. Status-based: build_glossary has its own gates."""
    from babelink.db import Database
    from babelink.importers.base import store_chapters
    from babelink.models import TerminologyEntry
    from babelink.segment import ParsedChapter, to_html
    from babelink.terminology import build_glossary, reconcile_glossary

    db = Database(tmp_path / "p.db")
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    doc = db.add_document(pid, "d", "eval", "d")
    paras = ["地下有寶。", "地下有光。", "地下有人。", "龍舟來了。", "龍舟去了。", "龍舟走了。"]
    store_chapters(db, pid, doc, [ParsedChapter("c", to_html(paras), paras)])
    outs = ["There is hidden treasure.", "Light below ground.", "Someone is down there.",
            "The Dragon Boat arrived.", "The Dragon Boat left.", "The Dragon Boat sailed."]
    for s, out in zip(db.segments_for_project(pid), outs):
        db.update_segment(s["id"], translated=out)
    db.add_terminology(TerminologyEntry(None, pid, ["地下"], {"en": "Underground"}, "other", "preferred", "suggested", 9))
    db.add_terminology(TerminologyEntry(None, pid, ["寶"], {"en": "Treasure"}, "object", "preferred", "suggested", 9))
    db.add_terminology(TerminologyEntry(None, pid, ["龍舟"], {"en": "Dragon Boat"}, "object", "preferred", "suggested", 9))
    won = reconcile_glossary(db, pid)
    assert won == {"龍舟": "Dragon Boat"}  # book converges verbatim: elected; 寶 used lowercase: kept, not elected
    statuses = {json.loads(t["source_forms"])[0]: t["status"] for t in db.terminology_for_project(pid)}
    assert statuses["地下"] == "ignored"  # no output uses any candidate surface
    assert statuses["寶"] == "suggested" and statuses["龍舟"] == "suggested"
    assert "地下" not in build_glossary(db, pid)


def test_entity_mask_restore_roundtrip():
    """E09 unit: nested forms mask once (longest first), proxies restore to
    renderings, unknown proxies are reported not dropped, E12/E1 never collide."""
    from babelink.entities import entities_for_segment, mask_entities, restore_proxies

    glossary = {"林黛玉": "Dai Yu", "黛玉": "Dai Yu", "賈政": "Jia Zheng"}
    src = "黛玉去見賈政，林黛玉笑了。"
    ents, renderings = entities_for_segment(glossary, src)
    masked, used = mask_entities(src, ents)
    assert len(used) == 3 and all(e in used for e in (0, 1, 2))
    assert "黛玉" not in masked and "林黛玉" not in masked and "賈政" not in masked
    assert masked.count("\u27e6E") == 3  # every mention masked exactly once
    out, missing = restore_proxies(f"{masked} and \u27e6E99\u27e7", renderings)
    assert "Dai Yu" in out and "Jia Zheng" in out and missing == [99]
    # E12 before E1: no partial collision
    out2, _ = restore_proxies("__E12__ and __E1__", {1: "A", 12: "B"})
    assert out2 == "B and A"
    # DG16 case: Hy-MT2 rewrites ⟦E4⟧ to [E4] in flight — still restored
    out3, missing3 = restore_proxies("When [E4] entered, [E4] saw.", {4: "Daiyu"})
    assert out3 == "When Daiyu entered, Daiyu saw." and missing3 == []
    # DG17 case: ?E4? rewrite — also restored
    out4, missing4 = restore_proxies("Although ?E4? was small, ?E4? spoke.", {4: "Daiyu"})
    assert out4 == "Although Daiyu was small, Daiyu spoke." and missing4 == []


def test_frequent_drops_get_a_second_solo_roll(tmp_path):
    """賈赦-class drops at 100+ occurrences get two solo attempts; rare drops
    get one (bounded helper cost)."""
    from babelink.terminology import build_glossary, suggest_term_translations, upsert_candidates

    class Flaky(MockProvider):
        def __init__(self):
            super().__init__()
            self.n = 0

        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            self.n += 1
            if len(items) > 1:  # batch keeps 林凡, drops 賈赦 by omission
                return [{"term": "林凡", "type": "character", "translation": "Lin Fan", "enforce": True}]
            return [{"term": items[0][0], "type": "character", "translation": "Jia She", "enforce": True}] \
                if self.n > 2 else []

    db, pid, _ = project(tmp_path, "賈赦來了林凡。\n\n賈赦去了林凡。\n\n賈赦又來了。")
    upsert_candidates(db, pid, [("賈赦", 150), ("林凡", 150)])
    assert suggest_term_translations(db, pid, Flaky(), retry_present={"賈赦"}) == 2
    assert build_glossary(db, pid) == {"賈赦": "Jia She", "林凡": "Lin Fan"}


def test_term_retry_rescues_batch_dropped_names(tmp_path):
    """Red Chamber ch3: 賈政/王夫人 fell out of 12-term batches (ignored, no
    rendering) but classify fine alone. Excerpt-present ignored terms get one
    solo second chance and return to suggested with a rendering."""
    from babelink.terminology import build_glossary, suggest_term_translations, upsert_candidates

    class BatchDrops(MockProvider):
        def __init__(self):
            super().__init__()
            self.calls_n = 0

        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            self.calls_n += 1
            if len(items) > 1:  # batch pass: competition drops half the names
                return [{"term": t, "type": "character", "translation": "Dai Yu", "enforce": True}
                        for t, _ in items[:1]]
            t, _ = items[0]
            return [{"term": t, "type": "character",
                     "translation": {"賈政": "Jia Zheng", "王夫人": "Wang FuRen"}.get(t, t),
                     "enforce": True}]

    db, pid, _ = project(tmp_path, "黛玉去見王夫人。\n\n賈政問如海。\n\n黛玉笑了，王夫人點頭。")
    upsert_candidates(db, pid, [("黛玉", 4), ("賈政", 3), ("王夫人", 3)])
    helper = BatchDrops()
    n = suggest_term_translations(db, pid, helper, retry_present={"黛玉", "賈政", "王夫人"})
    assert n == 3
    assert build_glossary(db, pid) == {"黛玉": "Dai Yu", "賈政": "Jia Zheng", "王夫人": "Wang Furen"}
    assert helper.calls_n == 1 + 2  # one batch + two solo rescues


def test_term_retry_leaves_true_junk_ignored(tmp_path):
    """Second chance must not resurrect junk: 三日 stays ignored even when
    present, and true drops (如此) never enter the glossary."""
    from babelink.terminology import build_glossary, suggest_term_translations, upsert_candidates

    class Honest(MockProvider):
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            ren = {"三日": ("other", "three days"), "如此": None}
            out = []
            for t, _ in items:
                r = ren.get(t)
                if r:
                    out.append({"term": t, "type": r[0], "translation": r[1], "enforce": True})
            return out

    db, pid, _ = project(tmp_path, "三日後。\n\n如此說來。\n\n三日又過。")
    upsert_candidates(db, pid, [("三日", 3), ("如此", 3)])
    assert suggest_term_translations(db, pid, Honest(), retry_present={"三日", "如此"}) == 0
    assert build_glossary(db, pid) == {}
    assert {t["status"] for t in db.terminology_for_project(pid)} == {"ignored"}


def test_helper_enforce_verdict_respected(tmp_path):
    """The helper's own enforce=false verdict ignores the term (but keeps the
    rendering stored), whatever the word lists say."""
    from babelink.terminology import build_glossary, suggest_term_translations, upsert_candidates

    class Judge(MockProvider):
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            return [{"term": t, "type": "character", "translation": "Lin Fan", "enforce": False} for t, _ in items]

    db, pid, _ = project(tmp_path, "林凡來了。\n\n林凡去了。\n\n林凡又來了。")
    upsert_candidates(db, pid, [("林凡", 3)])
    assert suggest_term_translations(db, pid, Judge()) == 0
    assert build_glossary(db, pid) == {}
    row = db.terminology_for_project(pid)[0]
    assert row["status"] == "ignored" and "Lin Fan" in row["canonical_translation"]


def test_reconcile_elects_in_context_winner(tmp_path):
    """The book votes: 黛玉's segments converge on Daiyu (Da Yu nowhere) so
    Daiyu becomes enforced; a split term (Yucun 2/3) stays unenforced."""
    from babelink.importers.base import store_chapters
    from babelink.segment import ParsedChapter, to_html
    from babelink.terminology import build_glossary, reconcile_glossary, suggest_term_translations, upsert_candidates

    db, pid, _ = project(tmp_path, "x")
    doc = db.add_document(pid, "d", "eval", "d")
    paras = ["黛玉來了，黛玉笑。", "黛玉去了，黛玉哭。", "黛玉又來，黛玉走。",
             "雨村來，雨村去。", "雨村笑，雨村哭。", "雨村走，雨村停。"]
    store_chapters(db, pid, doc, [ParsedChapter("c", to_html(paras), paras)])
    for s in db.segments_for_project(pid):
        i = s["order_index"]
        out = ("Daiyu came, Daiyu laughed." if i == 0 else
               "Daiyu went, Daiyu cried." if i == 1 else
               "Daiyu came again, Daiyu left." if i == 2 else
               "Yucun came, Yucun went." if i == 3 else
               "Yu Village laughed, Yu Village cried." if i == 4 else
               "Yucun walked, Yucun stopped.")
        db.update_segment(s["id"], translated=out)
    upsert_candidates(db, pid, [("黛玉", 6), ("雨村", 6)])

    class Helper2(MockProvider):
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            ren = {"黛玉": "Da Yu", "雨村": "Yu Jun"}
            return [{"term": t, "type": "character", "translation": ren[t], "enforce": True} for t, _ in items]

    class MT2(MockProvider):
        def translate(self, source, **kw):
            return {"黛玉": "Daiyu", "雨村": "Yucun"}[source]

    suggest_term_translations(db, pid, Helper2(), translator=MT2())
    won = reconcile_glossary(db, pid, min_occ=3)
    assert won == {"黛玉": "Daiyu"}, won  # 3/3 Daiyu; 雨村 split 2/3 stays out
    assert build_glossary(db, pid)["黛玉"] == "Daiyu"


def test_finalize_rewrites_losing_renderings(tmp_path):
    """End of the vote loop: losers kept on reconcile, then normalize rewrites
    'Yu Village'->'Yucun' in machine outputs, leaves human-approved text."""
    from babelink.importers.base import store_chapters
    from babelink.review import edit_segment
    from babelink.segment import ParsedChapter, to_html
    from babelink.terminology import build_glossary, finalize_terms, suggest_term_translations, upsert_candidates

    from babelink import service
    from babelink.db import Database

    db = Database(tmp_path / "d.db")  # no pasted chapter: segment order must match outs exactly
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    doc = db.add_document(pid, "d", "eval", "d")
    paras = ["雨村來了。", "雨村去了。", "雨村又來。", "雨村再來。", "雨村終來。"]
    store_chapters(db, pid, doc, [ParsedChapter("c", to_html(paras), paras)])
    outs = ["Yucun came.", "Yucun went.", "Yucun came again, Yu Jun left.",
            "Yucun returned.", "Yu Village arrived at last."]
    for s, out in zip(db.segments_for_project(pid), outs):
        db.update_segment(s["id"], translated=out)
    edit_segment(db, pid, db.segments_for_project(pid)[4]["id"], "Yu Village arrived at last, indeed.")
    upsert_candidates(db, pid, [("雨村", 6)])

    class Helper(MockProvider):
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            return [{"term": t, "type": "character", "translation": "Yu Jun", "enforce": True} for t, _ in items]

    class MT(MockProvider):
        def translate(self, source, **kw):
            return "Yucun"

    suggest_term_translations(db, pid, Helper(), translator=MT())
    won = finalize_terms(db, pid)
    assert won == {"雨村": "Yucun"}, won
    texts = [s["translated_text"] for s in db.segments_for_project(pid)]
    assert texts[0] == "Yucun came."
    assert texts[1] == "Yucun went."
    assert texts[2] == "Yucun came again, Yucun left."
    assert texts[3] == "Yucun returned."
    assert texts[4] == "Yu Village arrived at last, indeed."  # human wording untouched
    assert build_glossary(db, pid)["雨村"] == "Yucun"


def test_measure_drift_scores_book_consistency(tmp_path):
    """Drift: enforced 雨村=Yucun in 2/3 translated segments -> 66.7."""
    from babelink.importers.base import store_chapters
    from babelink.segment import ParsedChapter, to_html
    from babelink.terminology import build_glossary, measure_drift

    from babelink.db import Database
    from babelink.models import TerminologyEntry

    db = Database(tmp_path / "d.db")
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    doc = db.add_document(pid, "d", "eval", "d")
    paras = ["雨村來。", "雨村去。", "雨村走。"]
    store_chapters(db, pid, doc, [ParsedChapter("c", to_html(paras), paras)])
    for s, out in zip(db.segments_for_project(pid), ["Yucun came.", "Yucun went.", "Yu Jun walked."]):
        db.update_segment(s["id"], translated=out)
    db.add_terminology(TerminologyEntry(None, pid, ["雨村"], {"en": "Yucun"}, "character", "preferred", "confirmed", 3))
    assert build_glossary(db, pid) == {"雨村": "Yucun"}
    assert measure_drift(db, pid) == {"score": 66.7, "terms": {"雨村": {"hits": 2, "total": 3}}}


def test_suggest_spends_budget_on_multichar_first(tmp_path):
    """Single-char function words (的/是) outrank names by count but must not eat
    the helper budget: multi-char terms classify first."""
    from babelink.db import Database
    from babelink.providers.base import MockProvider
    from babelink.terminology import build_glossary, suggest_term_translations, upsert_candidates

    db = Database(tmp_path / "d.db")
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    upsert_candidates(db, pid, [("的", 3000), ("是", 1500), ("黛玉", 6), ("賈政", 5)])

    class Helper(MockProvider):
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            ren = {"黛玉": "Dai Yu", "賈政": "Jia Zheng"}
            return [{"term": t, "type": "character", "translation": ren[t],
                     "enforce": True} for t, _ in items]

    suggest_term_translations(db, pid, Helper(), limit=2)
    assert set(build_glossary(db, pid)) == {"黛玉", "賈政"}, build_glossary(db, pid)


def test_enforce_false_clean_rendering_becomes_vote_candidate(tmp_path):
    """Helper drops 賈赦 (enforce=false) but 'Jia She' is well-formed: stored as a
    vote candidate so reconcile can elect it; junk ('Small Boy') is not."""
    import json

    from babelink.db import Database
    from babelink.providers.base import MockProvider
    from babelink.terminology import suggest_term_translations, upsert_candidates

    db = Database(tmp_path / "d.db")
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    upsert_candidates(db, pid, [("賈赦", 9), ("小廝", 8)])

    class Helper(MockProvider):
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            tr = {"賈赦": "Jia She", "小廝": "Small Boy"}[items[0][0]]
            return [{"term": items[0][0], "type": "character", "translation": tr, "enforce": False}]

    suggest_term_translations(db, pid, Helper())
    rows = {json.loads(t["source_forms"])[0]: t for t in db.terminology_for_project(pid)}
    assert json.loads(rows["賈赦"]["canonical_translation"]).get("candidates") == ["Jia She"]
    assert "candidates" not in json.loads(rows["小廝"]["canonical_translation"])


def test_title_pinyin_preserves_word_breaks():
    """Probe renderings become candidates verbatim-ish: 'LADY WANG' -> 'Lady
    Wang', never the distorted single token 'Ladywang' (letters-only tallying
    would elect the distortion over every correctly spaced output)."""
    from babelink.terminology import _title_pinyin

    assert _title_pinyin("yucun") == "Yucun"
    assert _title_pinyin("LADY WANG") == "Lady Wang"
    assert _title_pinyin("Lady Wang") == "Lady Wang"


def test_reconcile_prefers_verbatim_surface_of_winning_reading(tmp_path):
    """Norm-twins ('Ladywang' vs 'Lady Wang') tally as one reading; the elected
    surface is the outputs' verbatim majority ('Lady Wang' 3/3), so substring
    adherence and future prompts use the natural form."""
    import json

    from babelink.db import Database
    from babelink.importers.base import store_chapters
    from babelink.models import TerminologyEntry
    from babelink.segment import ParsedChapter, to_html
    from babelink.terminology import build_glossary, reconcile_glossary

    db = Database(tmp_path / "d.db")
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    doc = db.add_document(pid, "d", "eval", "d")
    paras = ["王夫人來。", "王夫人去。", "王夫人走。"]
    store_chapters(db, pid, doc, [ParsedChapter("c", to_html(paras), paras)])
    for s, out in zip(db.segments_for_project(pid),
                      ["Lady Wang came.", "Lady Wang went.", "Lady Wang walked."]):
        db.update_segment(s["id"], translated=out)
    db.add_terminology(TerminologyEntry(None, pid, ["王夫人"], {"en": "", "candidates": ["Ladywang", "Lady Wang"]},
                                       "character", "preferred", "ignored", 9))
    assert reconcile_glossary(db, pid) == {"王夫人": "Lady Wang"}
    assert build_glossary(db, pid)["王夫人"] == "Lady Wang"


def test_reconcile_mines_winner_from_outputs_not_candidates(tmp_path):
    """DG16 case: helper said 'Wang Furen', probe said 'Ladywang', but every
    shipped output says 'Lady Wang' — the winner is the outputs' verbatim
    surface, which no component proposed. Recorded twins become losers."""
    import json

    from babelink.db import Database
    from babelink.importers.base import store_chapters
    from babelink.models import TerminologyEntry
    from babelink.segment import ParsedChapter, to_html
    from babelink.terminology import build_glossary, reconcile_glossary

    db = Database(tmp_path / "d.db")
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    doc = db.add_document(pid, "d", "eval", "d")
    paras = ["王夫人來。", "王夫人去。", "王夫人走。"]
    store_chapters(db, pid, doc, [ParsedChapter("c", to_html(paras), paras)])
    for s, out in zip(db.segments_for_project(pid),
                      ["Lady Wang came.", "Lady Wang went.", "Lady Wang walked."]):
        db.update_segment(s["id"], translated=out)
    db.add_terminology(TerminologyEntry(None, pid, ["王夫人"], {"en": "Ladywang", "candidates": ["Ladywang", "Wang Furen"]},
                                       "character", "preferred", "ignored", 9))
    assert reconcile_glossary(db, pid) == {"王夫人": "Lady Wang"}
    assert build_glossary(db, pid)["王夫人"] == "Lady Wang"
    losers = json.loads(db.terminology_for_project(pid)[0]["canonical_translation"])["losers"]
    assert set(losers) == {"Ladywang", "Wang Furen"}


def test_romaji_authority_rejects_misread_kanji_names():
    """ja twin of the pinyin authority: Unihan kun/on (+rendaku, +vowel fold)
    must cover the rendering; misreads never enforce, table gaps fail open."""
    from babelink.terminology import _is_bad_translation, _pinyin_matches, _romaji_matches

    assert _romaji_matches("田中", "Tanaka")
    assert _romaji_matches("山本", "Yamamoto")
    assert _romaji_matches("本田", "Honda")  # rendaku TA→DA
    assert _romaji_matches("東京", "Tokyo")  # long-vowel fold TOUKYOU→TOKYO
    assert _romaji_matches("田中", "Mr. Tanaka")  # honorifics judged separately
    assert _romaji_matches("田中", "Tanaka-san")
    assert not _romaji_matches("山本", "Yamamoyo")
    assert not _romaji_matches("林", "Forest")
    assert not _romaji_matches("村上", "Murakama")
    assert _romaji_matches("高橋", "Takahashi")  # conjugation stems TAKAI→TAKA
    assert _romaji_matches("渡辺", "Watanabe")  # ateji: anchored ends (WATA...+BE)
    assert _romaji_matches("大阪", "Osaka")  # OO→O fold + SAKA in table
    assert not _romaji_matches("渡辺", "Watanaba")  # anchored ends fail: rejected
    assert _is_bad_translation("渡辺", "Watanaba", "en", "character", "ja")
    # gate wiring: misread kanji name rejected, good name kept
    assert _is_bad_translation("山本", "Yamamoyo", "en", "character", "ja")
    assert not _is_bad_translation("田中", "Tanaka", "en", "character", "ja")


def test_pinyin_authority_rejects_misread_names(tmp_path):
    """Unihan-backed gate: 黛玉='Da Yu' (dai≠da), 賈赦='Jiasha' (she≠sha),
    雨村='Yu Jun' (cun≠jun) never enter the glossary even with enforce=true;
    correct readings (Daiyu, Dai Yu, Jia She, Yucun, Wang Furen) pass, and the
    dropped misreads leave no vote candidates."""
    from babelink.db import Database
    from babelink.terminology import (_is_bad_translation, _pinyin_matches, build_glossary,
                                        suggest_term_translations, upsert_candidates)
    from babelink.providers.base import MockProvider
    import json

    assert _pinyin_matches("黛玉", "Daiyu")
    assert _pinyin_matches("黛玉", "Dai Yu")
    assert _pinyin_matches("賈赦", "Jia She")
    assert _pinyin_matches("雨村", "Yucun")
    assert _pinyin_matches("王夫人", "Wang Furen")
    # Descriptive words pass freely ('lady' claims no reading; 'lin' matches 林),
    # so title-style renderings are unaffected — junk is the junk gate's job:
    assert _pinyin_matches("林姑娘", "Lin Lady")
    assert not _pinyin_matches("黛玉", "Da Yu")
    assert not _pinyin_matches("賈赦", "Jiasha")
    assert not _pinyin_matches("雨村", "Yu Jun")
    assert not _pinyin_matches("雨村", "Ujimura")

    db = Database(tmp_path / "d.db")
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    upsert_candidates(db, pid, [("黛玉", 9), ("賈赦", 8)])

    class Helper(MockProvider):
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            tr = {"黛玉": "Da Yu", "賈赦": "Jiasha"}[items[0][0]]
            return [{"term": items[0][0], "type": "character", "translation": tr, "enforce": True}]

    assert suggest_term_translations(db, pid, Helper()) == 0
    assert build_glossary(db, pid) == {}
    for t in db.terminology_for_project(pid):
        assert "candidates" not in json.loads(t["canonical_translation"])


def test_seed_probe_candidates_rescues_helper_dropped_names(tmp_path):
    """E16: helper drops 賈赦 on every attempt (no verdict at all) — the MT
    model's own probe rendering seeds a vote candidate (phonetic-gated);
    probe hallucinations (Ujimura) never seed."""
    from babelink.db import Database
    from babelink.importers.base import store_chapters
    from babelink.providers.base import MockProvider
    from babelink.segment import ParsedChapter, to_html
    from babelink.terminology import suggest_term_translations, upsert_candidates
    import json

    db = Database(tmp_path / "d.db")
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    doc = db.add_document(pid, "d", "eval", "d")
    store_chapters(db, pid, doc, [ParsedChapter("c", to_html(["賈赦來了。" * 30]), ["賈赦來了。" * 30])])
    upsert_candidates(db, pid, [("賈赦", 30), ("雨村", 30), ("的", 300)])

    class DropAll(MockProvider):
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            return []  # helper drops everything, batch and solo

    class MT(MockProvider):
        def translate(self, source, glossary=None, context="", max_tokens=None, **kw):
            return {"賈赦": "Jia She", "雨村": "Ujimura"}.get(source, source)

    assert suggest_term_translations(db, pid, DropAll(), translator=MT()) == 0
    cands = {json.loads(t["source_forms"])[0]: json.loads(t["canonical_translation"]).get("candidates", [])
             for t in db.terminology_for_project(pid)}
    assert cands["賈赦"] == ["Jia She"], cands  # probe habit seeded
    assert cands["雨村"] == [], cands  # hallucination filtered by phonetic gate
    assert cands["的"] == [], cands  # single-char function word never seeded


def test_seed_probe_candidates_ja_romaji_and_caps(tmp_path):
    """E16 edges: ja probes seed through the romaji gate; min_occ/cap bound
    the spend; long probe rambles truncate to a name."""
    from babelink.db import Database
    from babelink.providers.base import MockProvider
    from babelink.terminology import _seed_probe_candidates, upsert_candidates
    import json

    db = Database(tmp_path / "d.db")
    pid = db.create_project("t", source_lang="ja", target_lang="en")
    upsert_candidates(db, pid, [("田中", 50), ("山本", 50), ("東京", 5), ("林", 60)])
    for t in db.terminology_for_project(pid):
        db.update_terminology(t["id"], status="ignored")

    class MT(MockProvider):
        def translate(self, source, glossary=None, context="", max_tokens=None, **kw):
            return {"田中": "Tanaka", "山本": "Yamamoyo",
                    "東京": "Tokyo", "林": "Hayashi of the forest realm"}.get(source, source)

    _seed_probe_candidates(db, pid, MT(), "en", "ja", min_occ=20, cap=1)
    cands = {json.loads(t["source_forms"])[0]: json.loads(t["canonical_translation"]).get("candidates", [])
             for t in db.terminology_for_project(pid)}
    assert cands["田中"] == ["Tanaka"], cands  # romaji gate passes
    assert cands["山本"] == [], cands  # misread filtered (cap=1 spent on 田中 anyway)
    assert cands["東京"] == [], cands  # below min_occ
    assert cands["林"] == [], cands  # 'Hayashi of the forest realm' is not romaji-shaped
    # uncapped: 山本's misread still stays out; 東京 stays out (occ 5 < 20)
    _seed_probe_candidates(db, pid, MT(), "en", "ja", min_occ=20, cap=10)
    cands = {json.loads(t["source_forms"])[0]: json.loads(t["canonical_translation"]).get("candidates", [])
             for t in db.terminology_for_project(pid)}
    assert cands["山本"] == [], cands
    assert cands["東京"] == [], cands
    assert cands["林"] == [], cands


def test_review_confirmed_terms_flags_minority_enforcement(tmp_path):
    """Backward-revision trigger: 雨村 enforced 'Yu Jun' but shipped outputs say
    'Yucun' 3/3 -> flagged with the observed majority; a 2/3 term is not."""
    from babelink.importers.base import store_chapters
    from babelink.segment import ParsedChapter, to_html
    from babelink.terminology import review_confirmed_terms

    from babelink.db import Database
    from babelink.models import TerminologyEntry

    db = Database(tmp_path / "d.db")
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    doc = db.add_document(pid, "d", "eval", "d")
    paras = ["雨村來。", "雨村去。", "雨村走。", "賈政來。", "賈政去。", "賈政走。"]
    store_chapters(db, pid, doc, [ParsedChapter("c", to_html(paras), paras)])
    outs = ["Yucun came.", "Yucun went.", "Yucun walked.",
            "Jia Zheng came.", "Jia Zheng went.", "JiaZheng walked away."]
    for s, out in zip(db.segments_for_project(pid), outs):
        db.update_segment(s["id"], translated=out)
    db.add_terminology(TerminologyEntry(None, pid, ["雨村"], {"en": "Yu Jun"}, "character", "preferred", "confirmed", 3))
    db.add_terminology(TerminologyEntry(None, pid, ["賈政"], {"en": "Jia Zheng"}, "character", "preferred", "confirmed", 3))
    flagged = review_confirmed_terms(db, pid)
    assert [f["term"] for f in flagged] == ["雨村"], flagged
    assert flagged[0]["enforced"] == "Yu Jun" and flagged[0]["observed"] == "Yucun"


def test_romaji_gate_for_japanese(tmp_path):
    """ja twin of the pinyin gate: transliteration-shaped renderings enforce
    only for names (シャリ=Shari kept as character, dropped as other)."""
    from babelink.terminology import _is_bad_translation, _is_romaji_shaped

    assert _is_romaji_shaped("Shari") and _is_romaji_shaped("Henri")
    assert not _is_romaji_shaped("Almeria") and not _is_romaji_shaped("Knight")
    assert _is_bad_translation("シャリ", "Shari", "en", "other", "ja")
    assert not _is_bad_translation("シャリ", "Shari", "en", "character", "ja")


def test_demote_unused_terms(tmp_path):
    """A suggested term the model never applies gets demoted; applied and
    confirmed terms survive."""
    from babelink.importers.base import store_chapters
    from babelink.models import SegmentState
    from babelink.segment import ParsedChapter, to_html
    from babelink.terminology import build_glossary, demote_unused_terms, suggest_term_translations, upsert_candidates

    class Fixed(MockProvider):
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            return [{"term": t, "type": "character", "translation": {"林凡": "Lin Fan", "青雲宗": "Azure Cloud Sect"}[t]}
                    for t, _ in items]

    db = Database(tmp_path / "d.db")
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    doc = db.add_document(pid, "x", "eval", "x")
    paras = ["林凡走進青雲宗。", "林凡看到了青雲宗。", "林凡離開青雲宗。"]
    cids = store_chapters(db, pid, doc, [ParsedChapter("c", to_html(paras), paras)])
    upsert_candidates(db, pid, [("林凡", 3), ("青雲宗", 3)])
    assert suggest_term_translations(db, pid, Fixed()) == 2
    for s in db.segments_for_chapter(cids[0]):  # model applies Lin Fan, never Azure Cloud Sect
        db.update_segment(s["id"], translated="Lin Fan walked.", state=SegmentState.TRANSLATED)
    assert demote_unused_terms(db, pid) == 1
    assert build_glossary(db, pid) == {"林凡": "Lin Fan"}
    db.close()


def test_select_best_prefers_term_complete_draft():
    from babelink.pipeline import select_best

    src = "林凡走進青雲宗。"
    hits = {"林凡": "Lin Fan", "青雲宗": "Azure Cloud Sect"}
    bad = "He walked into the sect."
    good = "Lin Fan walked into the Azure Cloud Sect."
    assert select_best(src, [bad, good], hits, "zh") == good
    assert select_best(src, [good, bad], hits, "zh") == good  # order-stable


def test_agreement_ignores_leading_articles(tmp_path):
    """DH21: the probe's 'the old lady' vs the helper's 'Old Lady' demoted
    老太太-class names to vote-only — articles are grammar, not vocabulary,
    so agreement compares article-stripped. A real disagreement still demotes."""
    import json

    from babelink.terminology import build_glossary, suggest_term_translations, upsert_candidates
    from babelink.providers.base import MockProvider
    from test_quality import project

    class Helper(MockProvider):
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            ren = {"老太太": "Old Lady", "王夫人": "Lady Wang", "小廝": "Small Boy"}
            return [{"term": t, "type": "character", "translation": ren[t], "enforce": True} for t, _ in items]

    class MT(MockProvider):
        def translate(self, source, **kw):
            return {"老太太": "the old lady", "王夫人": "Lady Wang", "小廝": "a servant boy"}[source]

    db, pid, _ = project(tmp_path, "老太太來了。\n\n王夫人去了。\n\n小廝又來了。")
    upsert_candidates(db, pid, [(t, 3) for t in ("老太太", "王夫人", "小廝")])
    assert suggest_term_translations(db, pid, Helper(), translator=MT()) == 2
    assert build_glossary(db, pid) == {"老太太": "Old Lady", "王夫人": "Lady Wang"}


def test_retry_prefers_bursty_names_over_diffuse_words(tmp_path):
    """DH22: 那里 (in nearly every passage) ate solo-retry slots while 王夫人
    (bursty: many mentions, few passages) never got classified. Retry now
    spends diffuse-last, bursty-first — the mining dispersion statistic,
    applied to slot order."""
    from babelink.terminology import suggest_term_translations, upsert_candidates
    from babelink.providers.base import MockProvider
    from test_quality import project

    # 王夫人: 6 mentions in 2 passages (bursty). 那里: 6 mentions in 6 passages (diffuse).
    paras = ["王夫人說王夫人笑王夫人走。那里有人。", "王夫人又來說王夫人去。那里坐。",
             "那里吃那里喝。", "那里來那里去。", "那里看那里聽。", "那里站那里睡。"]
    db, pid, _ = project(tmp_path, "\n\n".join(paras))

    class Helper(MockProvider):
        seen = []
        def suggest_terms(self, items, source_lang="zh", target_lang="en"):
            Helper.seen.append([t for t, _ in items])
            return []  # batch drops everything: both go to retry

    upsert_candidates(db, pid, [("王夫人", 5), ("那里", 10)])
    # raw occurrences favor 那里 (10 > 5): without the dispersion rule the
    # diffuse word would take the single slot; with it the bursty name wins.
    suggest_term_translations(db, pid, Helper(), retry_present={"王夫人", "那里"}, retry_limit=1,
                              verbose=False)
    # only 1 slot: the bursty name must get it (its solo call happens), not the diffuse word
    assert Helper.seen and len(Helper.seen) == 2  # one batch call + one solo
    assert Helper.seen[-1] == ["王夫人"]
