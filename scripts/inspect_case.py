"""Inspect an eval case DB: which terms were mined, their status/translations,
and whether sources/outputs contain them. Usage: python scripts/inspect_case.py <case.db> [workdir]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from babelink.db import Database

db = Database(sys.argv[1])
chapter = int(sys.argv[2]) if len(sys.argv) > 2 else 0
pid = db.conn.execute("SELECT MAX(id) AS m FROM projects").fetchone()["m"]
print(f"latest project: {pid}")
from babelink.terminology import build_glossary  # noqa: E402

gloss = build_glossary(db, pid)
print(f"== effective glossary today: {len(gloss)} entries ==")
for k, v in sorted(gloss.items()):
    print(f"  {k} = {v}")
print("== QA findings ==")
for r in db.conn.execute(
        "SELECT severity, type, COUNT(*) n FROM qa_results WHERE project_id=? GROUP BY severity, type ORDER BY n DESC",
        (pid,)).fetchall():
    print(f"  {r['severity']}/{r['type']}: {r['n']}")
for r in db.conn.execute(
        "SELECT message FROM qa_results WHERE project_id=? AND type='possible_name_change'", (pid,)).fetchall():
    print(f"  NAME-CHANGE: {r['message']}")
print("== terminology ==")
for t in db.conn.execute(
        "SELECT id, source_forms, canonical_translation, status, concept, occurrences FROM terminology WHERE project_id=? ORDER BY occurrences DESC",
        (pid,)).fetchall():
    print(f"#{t['id']} {t['source_forms'][:60]} -> {t['canonical_translation'][:80]} [{t['status']}/{t['concept']}] x{t['occurrences']}")
print("== segments (first 4) ==")
q = ("SELECT s.source_text, s.translated_text, s.state FROM segments s JOIN chapters c ON c.id=s.chapter_id "
     "WHERE c.order_index=? ORDER BY s.order_index LIMIT 4") if chapter else \
    "SELECT source_text, translated_text, state FROM segments ORDER BY order_index LIMIT 4"
for s in db.conn.execute(q, (chapter - 1,) if chapter else ()).fetchall():
    print(f"SRC: {s['source_text'][:120]!r}")
    print(f"OUT: {(s['translated_text'] or '')[:160]!r} [{s['state']}]")
db.close()
