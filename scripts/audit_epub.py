"""Chapter-by-chapter EPUB audit: import testdata epub, check every chapter's
segments vs exporter paragraph mapping, round-trip export with mock, validate.
Usage: python scripts/audit_epub.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bs4 import BeautifulSoup

from lorebridge.db import Database
from lorebridge.exporters.epub import _replace_paragraphs
from lorebridge.exporters.validate import validate_epub, validate_project
from lorebridge.importers import import_epub
from lorebridge.segment import segment_tags
from lorebridge.service import open_workspace

SRC: Path  # resolved in main() from testdata/*.epub

issues = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        issues.append(msg)


def main():
    cands = sorted((Path(__file__).resolve().parent.parent / "testdata").glob("*.epub"))
    assert cands, "no epub in testdata/"
    SRC = cands[0]
    print(f"auditing {SRC.name} ({SRC.stat().st_size / 2**20:.1f} MB)")
    # Never on C:: full-book DBs + exports live on D:.
    root = Path(r"D:\StoryTranslation_data\tmp")
    root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="audit_", dir=str(root)))
    db = open_workspace(tmp)
    pid = db.create_project("audit", source_lang="en", target_lang="en")
    doc = import_epub(db, pid, SRC)
    chapters = db.chapters(pid)
    print(f"chapters={len(chapters)} doc={doc}")
    check(len(chapters) > 0, "book has chapters")
    total_mismatch = 0
    empty = 0
    for i, ch in enumerate(chapters):
        segs = db.segments_for_chapter(ch["id"])
        if not segs:
            empty += 1
            continue
        if ch["html"]:
            soup = BeautifulSoup(ch["html"], "lxml")
            tags = segment_tags(soup)
            n_tags, n_segs = len(tags), len(segs)
            if n_tags != n_segs:
                total_mismatch += 1
                if total_mismatch <= 10:
                    print(f"  ch{i+1} {ch['title'][:50]!r}: exporter tags={n_tags} != segments={n_segs}")
            # round-trip: replacing with identical text must preserve tag count
            texts = [s["source_text"] for s in segs]
            if n_tags == n_segs:
                out = _replace_paragraphs(ch["html"], texts, None, None, "en")
                n2 = len(segment_tags(BeautifulSoup(out, "lxml")))
                if n2 != n_segs:
                    print(f"  ch{i+1} round-trip drift: {n_segs} -> {n2}")
                    total_mismatch += 1
    check(empty == 0, f"no empty chapters (empty={empty})")
    print(f"tag/segment mismatches: {total_mismatch}")

    # mock full-book export with translations = source (no CJK issue for en book)
    from lorebridge.models import SegmentState
    db.conn.execute(
        "UPDATE segments SET translated_text = substr(source_text, 1, 200), state=? WHERE project_id=?",
        (SegmentState.TRANSLATED.value, pid))
    db.conn.commit()
    from lorebridge.exporters import export_epub
    out = tmp / "audit_out.epub"
    export_epub(db, pid, SRC, out)
    rep = validate_epub(out, "en")
    check(rep["ok"], f"exported epub validates: {rep['problems'] or 'ok'} ({rep['documents']} docs)")
    proj = validate_project(db, pid)
    print("project:", proj)
    db.close()
    print("ISSUES:", len(issues))
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
