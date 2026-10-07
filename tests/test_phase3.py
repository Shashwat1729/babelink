from pathlib import Path

from storytranslate.db import Database
from storytranslate.importers import import_txt
from storytranslate.pipeline import analyze_project, translate_project
from storytranslate.providers.base import MockProvider
from storytranslate.refinement import guardrail_ok, mark_stale_by_term, run_refinement
from storytranslate.models import SegmentState


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
