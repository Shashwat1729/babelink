"""Evaluation metrics + sentence-level benchmark runners (spec §57, §84).

Metrics (stdlib only):
- chrF++  character + word n-gram F-score (Popović 2015/2017), the standard cheap MT metric
- term adherence   share of glossary terms present in the source that appear in the output with the agreed rendering
- char_f1 / translation_f1   legacy token-overlap scores kept for the JSONL benchmark
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .providers.base import LLMProvider

_CJK_CHAR = re.compile(r"[一-鿿぀-ヿ가-힯]")


_WORD = re.compile(r"\w+|[^\w\s]")


def _ngrams(items: list, n: int) -> Counter:
    return Counter(tuple(items[i:i + n]) for i in range(len(items) - n + 1))


def chrf(hyp: str, ref: str, max_n: int = 6, word_order: int = 2, beta: float = 2.0) -> float:
    """chrF++ on a 0-100 scale (Popović 2015/2017): character 1..max_n-grams plus word 1..word_order-grams,
    precision and recall averaged over all orders, F-beta with beta=2 (recall counts double).
    Whitespace is ignored for character n-grams. Set word_order=0 for plain chrF."""
    h, r = re.sub(r"\s+", "", hyp), re.sub(r"\s+", "", ref)
    if not h or not r:
        return 0.0
    views = [(list(h), list(r), n) for n in range(1, max_n + 1)]
    if word_order:
        hw, rw = _WORD.findall(hyp.lower()), _WORD.findall(ref.lower())
        views += [(hw, rw, n) for n in range(1, word_order + 1)]
    precs, recs = [], []
    for hi, ri, n in views:
        hn, rn = _ngrams(hi, n), _ngrams(ri, n)
        if not hn or not rn:
            continue
        match = sum((hn & rn).values())
        precs.append(match / sum(hn.values()))
        recs.append(match / sum(rn.values()))
    if not precs:
        return 0.0
    p, rc = sum(precs) / len(precs), sum(recs) / len(recs)
    if p + rc == 0:
        return 0.0
    return 100 * (1 + beta ** 2) * p * rc / (beta ** 2 * p + rc)


def term_adherence(source: str, output: str, glossary: dict[str, str]) -> tuple[int, int]:
    """(terms rendered as agreed, terms that occur in the source)."""
    low = output.lower()
    present = [(k, v) for k, v in glossary.items() if k in source]
    return sum(1 for _, v in present if v.lower() in low), len(present)


def char_f1(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    common = sa & sb
    if not common:
        return 0.0
    p, r = len(common) / len(sa), len(common) / len(sb)
    return 2 * p * r / (p + r)


def translation_f1(output: str, reference: str) -> float:
    """Token-level F1. CJK tokenizes per-character; Latin per-word."""
    if _CJK_CHAR.search(reference):
        toks_a, toks_b = list(output), list(reference)
    else:
        toks_a, toks_b = output.lower().split(), reference.lower().split()
    if not toks_a or not toks_b:
        return 0.0
    ca, cb = Counter(toks_a), Counter(toks_b)
    common = sum((ca & cb).values())
    if not common:
        return 0.0
    p, r = common / len(toks_a), common / len(toks_b)
    return 2 * p * r / (p + r)


def _avg(xs: list[float]) -> float:
    return round(sum(xs) / max(len(xs), 1), 4)


def run_benchmark(records: list[dict], provider: LLMProvider, glossary: dict[str, str] | None = None, context: str = "") -> dict:
    f1s, chrfs = [], []
    for rec in records:
        out = provider.translate(rec["source"], glossary=glossary or {}, context=context or rec.get("context", ""),
                                 source_lang=rec.get("source_language", "zh"), target_lang=rec.get("target_language", "en"))
        f1s.append(translation_f1(out, rec["reference"]))
        chrfs.append(chrf(out, rec["reference"]))
    return {"model": provider.name, "samples": len(records), "avg_char_f1": _avg(f1s), "avg_chrf": round(_avg(chrfs), 2)}


def write_leaderboard(results: list[dict], out_path: str | Path) -> Path:
    lines = ["| Model | Samples | Avg Char F1 | chrF |", "|---|---|---|---|"]
    for r in sorted(results, key=lambda r: -r["avg_char_f1"]):
        lines.append(f"| {r['model']} | {r['samples']} | {r['avg_char_f1']} | {r.get('avg_chrf', '')} |")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def run_matrix(records: list[dict], provider: LLMProvider, project_dir: str, glossary: dict[str, str] | None = None) -> list[dict]:
    """Sentence-level ablation A, B, C, F (spec §57). D (TM) and E (hierarchical context) only mean something
    on a continuous text: see eval_real.run_corpus_eval, which runs all six."""
    from .context import gather_context, render_context

    glossary = glossary or {}
    configs = {
        "A_bare": dict(glossary=None, ctx=False, refine=False),
        "B_glossary": dict(glossary=glossary, ctx=False, refine=False),
        "C_glossary+bible": dict(glossary=glossary, ctx=True, refine=False),
        "F_full+refine": dict(glossary=glossary, ctx=True, refine=True),
    }
    results = []
    for label, cfg in configs.items():
        f1s, chrfs, hit, tot = [], [], 0, 0
        for rec in records:
            ctx = render_context(gather_context(project_dir, rec["source"], [], rec.get("context", ""))) if cfg["ctx"] else ""
            out = provider.translate(rec["source"], glossary=cfg["glossary"], context=ctx)
            if cfg["refine"]:
                out = provider.refine(rec["source"], out)
            f1s.append(translation_f1(out, rec["reference"]))
            chrfs.append(chrf(out, rec["reference"]))
            h, t = term_adherence(rec["source"], out, glossary)
            hit, tot = hit + h, tot + t
        results.append({"config": label, "model": provider.name, "samples": len(records), "avg_char_f1": _avg(f1s),
                        "avg_chrf": round(_avg(chrfs), 2), "term_adherence": round(hit / tot, 3) if tot else None})
    return results


def write_matrix_leaderboard(results: list[dict], out_path: str | Path) -> Path:
    lines = ["| Config | Model | Samples | Char F1 | chrF | Term adherence |", "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['config']} | {r['model']} | {r['samples']} | {r['avg_char_f1']} | {r.get('avg_chrf', '')} | {r.get('term_adherence', '')} |")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
