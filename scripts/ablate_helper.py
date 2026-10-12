"""Ablate the helper model on identical term-classification batches.
Usage: python scripts/ablate_helper.py [--models a b c]
Compares renderings + drop decisions, the upstream of glossary quality.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lorebridge import service  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--models", nargs="+", default=["huihui_ai/qwen3-abliterated:4b", "gemma3:1b"])
args = ap.parse_args()

ITEMS = [
    ("雨村", "卻說雨村忙回頭看時，不是別人，乃是當日同僚。"),
    ("黛玉", "卻說黛玉同姊妹們至王夫人處。"),
    ("賈母", "賈母命人將黛玉接來。"),
    ("如此", "如此說來，便是天意了。"),
    ("三日", "時卒已三日，母聞棺中呻吟。"),
    ("笑曰", "女笑曰：室有劍。"),
    ("且說", "且說那人去後，心中不快。"),
    ("上來", "老太太上來坐了。"),
    ("公子", "那公子走進門來。"),
    ("異史氏曰", "異史氏曰：此真異事也。"),
]

for model in args.models:
    p = service.make_provider("ollama", model)
    t0 = time.time()
    try:
        found = p.suggest_terms(ITEMS, "zh", "en")
    except Exception as e:  # noqa: BLE001
        print(f"== {model}: FAILED {e}")
        continue
    kept = {d["term"]: (d.get("type"), d.get("translation")) for d in found}
    dropped = [t for t, _ in ITEMS if t not in kept]
    print(f"== {model} ({time.time()-t0:.0f}s): kept {len(kept)}, dropped {dropped}")
    print(json.dumps(kept, ensure_ascii=False, indent=1))
