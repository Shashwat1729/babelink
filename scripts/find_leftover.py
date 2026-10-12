"""Find segments whose translation still contains source-script text.
Usage: python scripts/find_leftover.py <case.db>
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lorebridge.db import Database  # noqa: E402

CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")
db = Database(sys.argv[1])
for s in db.conn.execute("SELECT order_index, source_text, translated_text, state FROM segments ORDER BY order_index").fetchall():
    out = s["translated_text"] or ""
    if CJK.search(out):
        print(f"--- seg {s['order_index']} [{s['state']}]")
        print(f"SRC: {s['source_text'][:200]!r}")
        print(f"OUT: {out[:200]!r}")
db.close()
