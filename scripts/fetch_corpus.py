"""Download the public-domain evaluation corpus (Project Gutenberg EPUBs) into corpus/.

    python scripts/fetch_corpus.py

Files are git-ignored. License details: `babelink datasets` (src/babelink/dataset_manifest.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from babelink.dataset_manifest import MANIFEST  # noqa: E402

URL = "https://www.gutenberg.org/ebooks/{id}.epub.noimages"


def main(dest: str = "corpus") -> int:
    out = Path(dest)
    out.mkdir(exist_ok=True)
    for d in MANIFEST:
        if not d.get("gutenberg_id"):
            continue
        target = out / d["local_file"]
        if target.exists():
            print(f"have {target}")
            continue
        r = httpx.get(URL.format(id=d["gutenberg_id"]), follow_redirects=True, timeout=120, headers={"User-Agent": "babelink-corpus-fetch"})
        r.raise_for_status()
        target.write_bytes(r.content)
        print(f"fetched {target} ({len(r.content) // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
