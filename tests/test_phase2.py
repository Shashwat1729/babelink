import json
from pathlib import Path

from storytranslate.context import gather_context, render_context
from storytranslate.db import Database
from storytranslate.importers import import_txt
from storytranslate.pipeline import analyze_project, translate_project
from storytranslate.providers.base import MockProvider
from storytranslate.story_bible import load_section
from storytranslate.summaries import chapter_summary
from storytranslate.terminology import detect_contradictions


def _setup(tmp_path: Path):
    p = tmp_path / "novel.txt"
    p.write_text(
        "第一章 开始\n\n林凡修炼到了炼气期。\n\n筑基期是另一个境界。\n\n第二章 突破\n\n林凡进入了筑基期。\n",
        encoding="utf-8",
    )
    db = Database(tmp_path / "project.db")
    pid = db.create_project("t", target_lang="en")
    import_txt(db, pid, p)
    return db, pid


def test_story_bible_written(tmp_path: Path):
    db, pid = _setup(tmp_path)
    info = analyze_project(db, pid, project_dir=tmp_path)
    assert info["language"] == "zh"
    terms = load_section(tmp_path, "terminology")
    assert isinstance(terms, list)
    summaries = load_section(tmp_path, "chapter_summaries")
    assert len(summaries) == 2
    arcs = load_section(tmp_path, "arc_summaries")
    assert len(arcs) == 1
    db.close()


def test_context_retrieval(tmp_path: Path):
    db, pid = _setup(tmp_path)
    analyze_project(db, pid, project_dir=tmp_path)
    translate_project(db, pid, MockProvider(), project_dir=tmp_path)
    items = gather_context(tmp_path, "林凡在筑基期", ["林凡修炼到了炼气期。"], "第一章：林凡入门。")
    assert any(i.kind == "previous_segment" for i in items)
    assert render_context(items)
    db.close()


def test_chapter_summary_extractive():
    s = chapter_summary(["他出生了。林家灭门。他修炼炼气期。"])
    assert s


def test_contradictions_detected(tmp_path: Path):
    db, pid = _setup(tmp_path)
    analyze_project(db, pid, project_dir=tmp_path)
    # force a contradictory canonical translation for the same source form
    row = db.conn.execute("SELECT * FROM terminology LIMIT 1").fetchone()
    if row:
        db.add_terminology(
            __import__("storytranslate.models", fromlist=["TerminologyEntry"]).TerminologyEntry(
                None, pid, json.loads(row["source_forms"]), {"en": "DIFFERENT"}, "", "preferred", "suggested", 1
            )
        )
        conflicts = detect_contradictions(db, pid, str(tmp_path))
        assert conflicts
    db.close()
