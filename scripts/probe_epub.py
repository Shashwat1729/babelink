"""Probe EPUB import step by step with timings (find the slow part).
Usage: python scripts/probe_epub.py
"""
import glob
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

t0 = time.time()


def mark(msg):
    print(f"{time.time()-t0:7.1f}s {msg}", flush=True)


cands = sorted(glob.glob(str(Path(__file__).resolve().parent.parent / "testdata" / "*.epub")))
print("file:", cands[0])
import ebooklib
from ebooklib import epub

mark("import ebooklib")
book = epub.read_epub(cands[0])
mark("read_epub done")
print("spine len:", len(book.spine))
docs = [book.get_item_with_id(i) for i, _ in book.spine]
docs = [d for d in docs if d is not None and d.get_type() == ebooklib.ITEM_DOCUMENT]
print("spine docs:", len(docs))
sizes = sorted((len(d.get_content()), d.get_id()) for d in docs)
print("smallest:", sizes[0][0], "largest:", sizes[-1][0], "total MB:", round(sum(s for s, _ in sizes) / 2**20, 2))
from lorebridge.segment import stream_from_html

t1 = time.time()
n = 0
for i, d in enumerate(docs):
    html = d.get_content().decode("utf-8", errors="replace")
    stream_from_html(html)
    n += 1
    if time.time() - t1 > 20:
        mark(f"stream_from_html timed: {n}/{len(docs)} docs in 20s+ (STOPPING EARLY)")
        break
else:
    mark(f"stream_from_html all {n} docs ok")
