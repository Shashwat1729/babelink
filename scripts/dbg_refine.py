"""Repro: refine name-change warning. Usage: python scripts/dbg_refine.py"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from babelink.db import Database
from babelink.importers import import_txt
from babelink.pipeline import analyze_project, translate_project
from babelink.providers.base import MockProvider
from babelink.refinement import dropped_names, run_refinement

tmp = Path(tempfile.mkdtemp(dir=r"D:\StoryTranslation_data\tmp"))


class Corrupt(MockProvider):
    def refine(self, source, draft, **kw):
        return draft.replace("Ruhai", "Ruihai")


db = Database(tmp / "n.db")
pid = db.create_project("n", source_lang="zh", target_lang="en")
p = tmp / "names.txt"
p.write_text("第一章\n\nLin Ruhai來了。\n\n他走了。\n", encoding="utf-8")
import_txt(db, pid, p)
analyze_project(db, pid, project_dir=str(tmp))
translate_project(db, pid, MockProvider(), project_dir=str(tmp))
for s in db.conn.execute("SELECT id, source_text, translated_text, state FROM segments").fetchall():
    print(dict(s))
print("dropped:", dropped_names("Lin Ruhai來了。", "Lin Ruihai來了。"))
n = run_refinement(db, pid, Corrupt(), str(tmp))
print("refined:", n)
for r in db.conn.execute("SELECT segment_id, type, message FROM qa_results").fetchall():
    print(dict(r))
db.close()
