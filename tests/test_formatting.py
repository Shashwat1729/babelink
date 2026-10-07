from pathlib import Path

from storytranslate.db import Database
from storytranslate.exporters.epub import _replace_paragraphs
from storytranslate.exporters.txt import export_txt
from storytranslate.importers import import_txt
from storytranslate.models import Chapter, Segment, SegmentState
from storytranslate.pipeline import analyze_project, translate_project
from storytranslate.providers.base import MockProvider


def test_replace_paragraphs_preserves_tag_attributes():
    html = '<html><body><p class="center">第一段。</p><p style="text-align:left">第二段。</p></body></html>'
    out = _replace_paragraphs(html, ["First.", "Second."])
    assert 'class="center"' in out
    assert 'style="text-align:left"' in out
    assert "First." in out and "Second." in out


def test_txt_spacing_single_blank_lines(tmp_path: Path):
    p = tmp_path / "n.txt"
    p.write_text("Chapter 1\n\n一。\n\n二。\n", encoding="utf-8")
    db = Database(tmp_path / "p.db")
    pid = db.create_project("t")
    import_txt(db, pid, p)
    analyze_project(db, pid)
    translate_project(db, pid, MockProvider())
    out = export_txt(db, pid, tmp_path / "out.txt")
    text = out.read_text(encoding="utf-8")
    assert "\n\n\n" not in text
    assert text.strip().endswith(("一。", "二。"))
    db.close()
