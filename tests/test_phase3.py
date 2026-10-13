from pathlib import Path

from babelink.db import Database
from babelink.importers import import_txt
from babelink.pipeline import analyze_project, translate_project
from babelink.providers.base import MockProvider
from babelink.refinement import guardrail_ok, mark_stale_by_term, run_refinement
from babelink.models import SegmentState


def _setup(tmp_path: Path):
    p = tmp_path / "novel.txt"
    p.write_text("第一章 开始\n\n林凡修炼到了炼气期，突破到了炼气期。\n\n他感觉到了异样的气息。\n", encoding="utf-8")
    db = Database(tmp_path / "project.db")
    pid = db.create_project("t", target_lang="en")
    import_txt(db, pid, p)
    analyze_project(db, pid, project_dir=tmp_path)
    translate_project(db, pid, MockProvider(), project_dir=tmp_path)
    return db, pid


def test_guardrail_keeps_terms():
    ok, _ = guardrail_ok("他突破了。", "He broke through to the Qi Refining Stage.", "He broke through to the Qi Refining Stage.", ["Qi Refining Stage"])
    assert ok
    ok, reason = guardrail_ok("他突破了。", "He broke through to the Qi Refining Stage.", "He leveled up.", ["Qi Refining Stage"])
    assert not ok and "canonical term lost" in reason


def test_refinement_with_mock_identity(tmp_path: Path):
    db, pid = _setup(tmp_path)
    n = run_refinement(db, pid, MockProvider(), str(tmp_path))
    assert n == 2
    states = {r["state"] for r in db.conn.execute("SELECT state FROM segments").fetchall()}
    assert states == {"REFINED"}
    db.close()


def test_mark_stale_by_term(tmp_path: Path):
    db, pid = _setup(tmp_path)
    n = mark_stale_by_term(db, pid, "炼气期", "Qi Condensation Stage")
    assert n >= 1
    states = {r["state"] for r in db.conn.execute("SELECT state FROM segments").fetchall()}
    assert "STALE" in states
    # retranslate picks up stale segments only (skips TM -> fresh translation)
    n2 = translate_project(db, pid, MockProvider(), project_dir=tmp_path)
    assert n2 == n
    db.close()


def test_refine_note_is_stored_apart_and_exported(tmp_path: Path):
    from babelink.exporters import export_epub, export_txt

    class Noting(MockProvider):
        def refine(self, source, draft, **kw):
            return draft + "\nNOTE: 炼气期 is the first cultivation realm."

    db, pid = _setup(tmp_path)
    assert run_refinement(db, pid, Noting(), str(tmp_path)) == 2
    rows = db.conn.execute("SELECT translated_text, translator_note FROM segments").fetchall()
    assert all("NOTE:" not in (r["translated_text"] or "") for r in rows)
    assert all(r["translator_note"] == "炼气期 is the first cultivation realm." for r in rows)
    txt = export_txt(db, pid, tmp_path / "out.txt").read_text(encoding="utf-8")
    assert "Translator's note: 炼气期 is the first cultivation realm." in txt
    out = tmp_path / "out.epub"
    export_epub(db, pid, None, out)
    from ebooklib import ITEM_DOCUMENT, epub as _epub

    book = _epub.read_epub(str(out))
    docs = b" ".join(i.get_content() for i in book.get_items_of_type(ITEM_DOCUMENT))
    assert b"Translator's note:" in docs
    db.close()


def test_refinement_flags_changed_names(tmp_path: Path):
    from babelink.refinement import dropped_names, run_refinement

    assert dropped_names("Ask Lin Ruhai for help. He left.", "Ask Lin Ruihai for help. He left.") == ["Ruhai"]
    assert dropped_names("After dinner, Yuzhen left.", "Having eaten, Yuzhen left.") == []
    assert dropped_names("He saw Zhang Rugui there.", "He saw Zhang Rugu there.") == ["Rugui"]

    class Corrupt(MockProvider):
        def refine(self, source, draft, **kw):
            return draft.replace("Ruhai", "Ruihai")

    db = Database(tmp_path / "names.db")
    pid = db.create_project("n", source_lang="zh", target_lang="en")
    p = tmp_path / "names.txt"
    p.write_text("第一章\n\nLin Ruhai來了。\n\n他走了。\n", encoding="utf-8")
    import_txt(db, pid, p)
    analyze_project(db, pid, project_dir=tmp_path)
    translate_project(db, pid, MockProvider(), project_dir=tmp_path)
    run_refinement(db, pid, Corrupt(), str(tmp_path))
    rows = db.conn.execute("SELECT type, message FROM qa_results WHERE type='possible_name_change'").fetchall()
    assert rows and "Ruhai" in rows[0]["message"]
    db.close()


def test_propagation_impact_previews_stale_scope(tmp_path: Path):
    from babelink.refinement import mark_stale_by_term, propagation_impact

    db, pid = _setup(tmp_path)
    impact = propagation_impact(db, pid, "炼气期")
    assert impact["segments"] >= 1 and impact["chapters"] and len(impact["sample"]) <= 5
    assert mark_stale_by_term(db, pid, "炼气期", "Qi Condensation") == impact["segments"]
    db.close()
