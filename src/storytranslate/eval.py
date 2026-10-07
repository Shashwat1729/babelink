"""Benchmark runner: compare configurations over a local JSONL dataset (spec §57)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .datasets import LocalFileAdapter, normalize_record
from .providers.base import LLMProvider

_CJK = re.compile(r"[一-鿿]")


def char_f1(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    common = sa & sb
    if not common:
        return 0.0
    p, r = len(common) / len(sa), len(common) / len(sb)
    return 2 * p * r / (p + r)


def run_benchmark(records: list[dict], provider: LLMProvider, glossary: dict[str, str] | None = None, context: str = "") -> dict:
    scores = []
    for rec in records:
        out = provider.translate(rec["source"], glossary=glossary or {}, context=context or rec.get("context", ""))
        scores.append(char_f1(out, rec["reference"]))
    return {"model": provider.name, "samples": len(records), "avg_char_f1": round(sum(scores) / max(len(scores), 1), 4)}


def write_leaderboard(results: list[dict], out_path: str | Path) -> Path:
    lines = ["| Model | Samples | Avg Char F1 |", "|---|---|---|"]
    for r in sorted(results, key=lambda r: -r["avg_char_f1"]):
        lines.append(f"| {r['model']} | {r['samples']} | {r['avg_char_f1']} |")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
