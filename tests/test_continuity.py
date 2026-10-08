"""Chapter-wise paste must continue the project: glossary, TM, summaries and previous passages carry over."""
import json

from lorebridge import service
from lorebridge.context import build_context
from lorebridge.models import SegmentState
from lorebridge.pipeline import translate_project
from lorebridge.providers.base import MockProvider
from lorebridge.story_bible import project_root, save_section
from lorebridge.terminology import build_glossary, set_term

CH1 = "林凡走进青云宗。\n\n林凡看见大师兄。\n\n大师兄对林凡说：“欢迎。”\n\n林凡点了点头。"
CH2 = "林凡点了点头。\n\n林凡离开了山门。"  # first paragraph repeats chapter 1 -> exact TM hit


def new_project(tmp_path):
    db = service.open_workspace(tmp_path)
    pid = service.create_project(db, "t", "zh", "en")
    return db, pid, project_root(tmp_path, pid)


def lin_fan(db, pid):
    return next(t for t in db.terminology_for_project(pid) if "林凡" in json.loads(t["source_forms"]))


def test_second_chapter_reuses_everything_from_the_first(tmp_path):
    db, pid, root = new_project(tmp_path)
    service.add_pasted_chapter(db, tmp_path, pid, CH1, "第一章")
    set_term(db, pid, lin_fan(db, pid)["id"], "Lin Fan", "confirmed")
    save_section(root, "characters", [{"origin": "user", "source": "大师兄", "translation": "Senior Brother", "gender": "male"}])
    translate_project(db, pid, MockProvider(), project_dir=root)

    (ch2,) = service.add_pasted_chapter(db, tmp_path, pid, CH2, "第二章")
    assert sum("林凡" in json.loads(t["source_forms"]) for t in db.terminology_for_project(pid)) == 1  # no duplicate on re-analysis
    mock = MockProvider()
    n = translate_project(db, pid, mock, project_dir=root, chapter_id=ch2)

    assert n == 2
    calls = [c for c in mock.calls if c["source"] != "第二章"]  # the chapter title is translated separately
    assert len(calls) == 1, "the repeated paragraph came from translation memory, not the model"
    call = calls[0]
    assert call["source"] == "林凡离开了山门。"
    assert call["glossary"] == {"林凡": "Lin Fan"}
    assert "林凡点了点头。 => " in call["context"], "last translated pair of chapter 1 is in the prompt"
    assert "[Story so far]" in call["context"]
    segs = db.segments_for_chapter(ch2)
    assert segs[0]["translated_text"] == "Lin Fan点了点头。"  # TM reuse keeps the earlier rendering


def test_context_lists_bible_notes_only_for_entities_present(tmp_path):
    db, pid, root = new_project(tmp_path)
    service.add_pasted_chapter(db, tmp_path, pid, CH1, "第一章")
    save_section(root, "characters", [{"origin": "user", "source": "大师兄", "translation": "Senior Brother", "gender": "male", "notes": "kind"},
                                      {"origin": "user", "source": "师姐", "translation": "Senior Sister", "gender": "female"}])
    segs = db.segments_for_chapter(db.chapters(pid)[0]["id"])
    ctx = build_context(db, pid, segs[2], root)
    assert "大师兄 = Senior Brother" in ctx and "gender: male" in ctx
    assert "师姐" not in ctx


def test_edited_term_marks_segments_stale_but_never_human_approved(tmp_path):
    db, pid, root = new_project(tmp_path)
    service.add_pasted_chapter(db, tmp_path, pid, CH1, "第一章")
    term = lin_fan(db, pid)
    set_term(db, pid, term["id"], "Lin Fan", "confirmed")
    translate_project(db, pid, MockProvider(), project_dir=root)
    segs = db.segments_for_chapter(db.chapters(pid)[0]["id"])
    db.update_segment(segs[0]["id"], state=SegmentState.HUMAN_APPROVED)

    set_term(db, pid, term["id"], "Fan Lin")
    states = {s["order_index"]: s["state"] for s in db.segments_for_chapter(db.chapters(pid)[0]["id"])}
    assert states[0] == "HUMAN_APPROVED"
    assert states[1] == "STALE" and states[2] == "STALE"
    mock = MockProvider()
    translate_project(db, pid, mock, project_dir=root)
    assert build_glossary(db, pid)["林凡"] == "Fan Lin"
    assert all(c["glossary"].get("林凡") == "Fan Lin" for c in mock.calls if "林凡" in c["source"])
    assert db.segments_for_chapter(db.chapters(pid)[0]["id"])[0]["state"] == "HUMAN_APPROVED"  # untouched


def test_refusal_flags_segment_and_run_continues(tmp_path):
    db, pid, root = new_project(tmp_path)
    service.add_pasted_chapter(db, tmp_path, pid, CH1, "第一章")
    n = translate_project(db, pid, MockProvider(refuse=("看见",)), project_dir=root)
    states = [s["state"] for s in db.segments_for_chapter(db.chapters(pid)[0]["id"])]
    assert n == 3 and states.count("NEEDS_REVIEW") == 1
    assert any(q["type"] == "refusal" for q in db.qa_for_project(pid))
    # a re-run does not retry the flagged paragraph by itself
    mock = MockProvider()
    assert translate_project(db, pid, mock, project_dir=root) == 0
    assert not [c for c in mock.calls if "看见" in c["source"]]
