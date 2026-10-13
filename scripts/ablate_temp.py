"""Ablate Hy-MT2 sampling temperature on fixed samples (bare model only, no helper).
Usage: python scripts/ablate_temp.py [--dataset parallel-fiction] [--samples 3]
"""
import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HOME", r"D:\StoryTranslation_data\hf")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from babelink import service  # noqa: E402
from babelink.datasets import OpenSakuraAdapter, ParallelFictionAdapter  # noqa: E402
from babelink.eval import chrf  # noqa: E402
from babelink.hymt import SAMPLING  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--dataset", default="parallel-fiction")
ap.add_argument("--samples", type=int, default=3)
ap.add_argument("--provider", default="hymt")
ap.add_argument("--model", default="")
ap.add_argument("--temps", default="0.3,0.7", help="comma-separated temperatures to compare")
args = ap.parse_args()

recs = (ParallelFictionAdapter(split="train[:20]").load() if args.dataset == "parallel-fiction"
        else OpenSakuraAdapter(split="test[:20]").load())[:args.samples]
provider = service.make_provider(args.provider, args.model or service.DEFAULT_TRANSLATE_MODEL)
print("model:", provider.name, "official temp:", SAMPLING["temperature"])

for temp in [float(t) for t in args.temps.split(",")]:
    cs, t0 = [], time.time()
    for r in recs:
        paras = [p.strip() for p in r["source"].split("\n\n") if p.strip()][:6]
        # HymtOllamaProvider.translate reads the shared SAMPLING dict at call
        # time, so mutating it here overrides temperature for this call only.
        import babelink.hymt as H

        old = H.SAMPLING["temperature"]
        H.SAMPLING["temperature"] = temp
        try:
            out = provider.translate("\n\n".join(paras), source_lang=r["source_language"], target_lang=r["target_language"])
        finally:
            H.SAMPLING["temperature"] = old
        cs.append(chrf(out, r["reference"]))
    print(f"temp={temp}: mean chrF={sum(cs)/len(cs):.2f} {' '.join(f'{c:.1f}' for c in cs)} ({time.time()-t0:.0f}s)")
