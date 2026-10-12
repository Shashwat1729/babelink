"""PF chapter length distribution (scope the full-chapter proof).
Usage: python scripts/pf_lengths.py [--rows 200]
"""
import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HOME", r"D:\StoryTranslation_data\hf")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

ap = argparse.ArgumentParser()
ap.add_argument("--rows", type=int, default=200)
args = ap.parse_args()

from datasets import load_dataset  # noqa: E402

ds = load_dataset("NilanE/ParallelFiction-Ja_En-100k", split=f"train[:{args.rows}]")
lens = sorted(len(r["src"]) for r in ds)
print(f"rows={len(lens)} min={lens[0]} p50={lens[len(lens)//2]} p90={lens[int(len(lens)*0.9)]} max={lens[-1]} chars (ja source)")
tlens = sorted(len(r["trg"]) for r in ds)
print(f"ref: min={tlens[0]} p50={tlens[len(tlens)//2]} p90={tlens[int(len(tlens)*0.9)]} max={tlens[-1]} chars (en ref)")
