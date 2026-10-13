"""Refine + score an already-translated eval DB (avoids redoing translate).
Usage: python scripts/refine_and_score.py <project.db> <zh.epub> <en.epub> <chapter> <paras> [--helper-model ...]
Prints chrF/terms/Harness Score before-vs-after + notes added.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from babelink import service  # noqa: E402
from babelink.db import Database  # noqa: E402
from babelink.eval import chrf, term_adherence  # noqa: E402
from babelink.eval_real import align_reference, find_chapter, harness_score  # noqa: E402
from babelink.importers import import_epub  # noqa: E402
from babelink.refinement import run_refinement  # noqa: E402
from babelink.service import open_workspace  # noqa: E402
from babelink.terminology import build_glossary  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("db")
ap.add_argument("zh_epub")
ap.add_argument("en_epub")
ap.add_argument("chapter", type=int)
ap.add_argument("paras", type=int)
ap.add_argument("--helper-model", default="")
ap.add_argument("--skip-refine", action="store_true", help="score the current DB text without refining")
args = ap.parse_args()

db = Database(args.db)
pid = db.conn.execute("SELECT MAX(id) AS m FROM projects").fetchone()["m"]
ch = db.conn.execute("SELECT * FROM chapters WHERE project_id=? ORDER BY order_index", (pid,)).fetchall()[-1]
before = ["\n".join((s["translated_text"] or "") for s in db.segments_for_chapter(ch["id"]) if s["translated_text"])]

helper = service.helper_for(service.make_provider("hymt", service.DEFAULT_TRANSLATE_MODEL),
                            "hymt", "", args.helper_model or "")
t0 = time.time()
n = 0 if args.skip_refine else run_refinement(db, pid, helper, chapter_id=ch["id"])
dt = time.time() - t0
after = "\n".join(s["translated_text"] or "" for s in db.segments_for_chapter(ch["id"]))
notes = sum(1 for s in db.segments_for_chapter(ch["id"]) if s["translator_note"])
glossary = build_glossary(db, pid)

tmp = Path(args.db).parent / "refalign"
zdb, edb = open_workspace(tmp), open_workspace(tmp / "e")
zpid = zdb.create_project("z", source_lang="zh", target_lang="en")
epid = edb.create_project("e", source_lang="en", target_lang="en")
import_epub(zdb, zpid, args.zh_epub)
import_epub(edb, epid, args.en_epub)
zch = find_chapter(zdb, zpid, args.chapter)
ech = find_chapter(edb, epid, args.chapter)
texts = [s["source_text"] for s in zdb.segments_for_chapter(zch["id"])]
en_paras = [s["source_text"] for s in edb.segments_for_chapter(ech["id"])]
reference = align_reference([len(t) for t in texts], min(args.paras, len(texts)), en_paras)

for label, hyp in (("before", before[0]), ("after", after)):
    import re as _re
    hb, ht = term_adherence("\n".join(texts[:args.paras]), hyp, glossary)
    cjk = sum(1 for p in hyp.split("\n\n") if _re.search(r"[一-鿿぀-ヿ가-힯]", p))
    print(f"{label}: chrF={chrf(hyp, reference):.2f} terms={hb}/{ht} "
          f"score={harness_score(hb, ht, cjk, args.paras, 0, 0)}")
print(f"refined {n} segments in {dt:.0f}s, notes added: {notes}")
zdb.close()
edb.close()
db.close()
