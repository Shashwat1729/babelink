"""Bare model vs full harness, side by side (stdlib + project only).

For each sample: (1) translate the text directly with the model (no glossary,
no context, no memory) and (2) run it through the real pipeline
(mine terms -> helper classifies -> glossary -> translate with context/TM).
Scores both against the reference with chrF++ and term adherence, so the
report shows what the MODEL does versus what the HARNESS adds.

Datasets land under D:\\StoryTranslation_data (HF_HOME pointed there so C: stays free).

Usage:
  set HF_HOME=D:\\StoryTranslation_data\\hf
  python scripts/eval_harness_vs_bare.py --dataset parallel-fiction --samples 5 --provider hymt
  python scripts/eval_harness_vs_bare.py --dataset opensakura --samples 10 --provider hymt --split "test[:10]"
  python scripts/eval_harness_vs_bare.py --local data/bench.jsonl --samples 5
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HOME", r"D:\StoryTranslation_data\hf")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lorebridge import service  # noqa: E402
from lorebridge.datasets import ColumnAdapter, LocalFileAdapter, OpenSakuraAdapter, ParallelFictionAdapter  # noqa: E402
from lorebridge.db import Database  # noqa: E402
from lorebridge.eval import chrf, term_adherence  # noqa: E402
from lorebridge.importers.base import store_chapters  # noqa: E402
from lorebridge.models import SegmentState  # noqa: E402
from lorebridge.pipeline import analyze_project, translate_project  # noqa: E402
from lorebridge.qa import check_segment  # noqa: E402
from lorebridge.segment import ParsedChapter, to_html  # noqa: E402
from lorebridge.terminology import build_glossary  # noqa: E402

_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")
DATA_ROOT = Path(os.environ.get("LOREBRIDGE_DATA", r"D:\StoryTranslation_data"))


def split_paras(text: str, cap: int = 12) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if len(paras) < 3:  # single-block chapter: split long lines
        paras = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if cap <= 0:  # whole chapter (bounded: very long chapters still cap at 60)
        return paras[:60] or [text.strip()[:2000]]
    return paras[:cap] or [text.strip()[:2000]]


def align_reference_window(source: str, excerpt: str, reference: str) -> str:
    """Score the excerpt against the matching head window of the reference
    (proportional by characters), not the whole chapter: otherwise chrF is
    dominated by coverage fraction and a good head translation reads as ~10."""
    ref_paras = [p.strip() for p in re.split(r"\n\s*\n", reference.strip()) if p.strip()]
    if len(ref_paras) < 3:
        ref_paras = [ln.strip() for ln in reference.strip().splitlines() if ln.strip()]
    frac = min(1.0, len(excerpt) / max(len(source), 1))
    budget = max(1, int(len(reference) * frac))
    window, total = [], 0
    for p in ref_paras:
        window.append(p)
        total += len(p)
        if total >= budget:
            break
    return "\n\n".join(window) or reference[:budget]


def load_records(args) -> list[dict]:
    if args.local:
        return LocalFileAdapter(args.local).load()
    if args.dataset == "parallel-fiction":
        return ParallelFictionAdapter(split=args.split).load()
    if args.dataset == "opensakura":
        return OpenSakuraAdapter(split=args.split).load()
    raise SystemExit(f"unknown dataset {args.dataset!r} (parallel-fiction|opensakura)")


def gold_glossary(rec: dict) -> dict[str, str]:
    return {t["term"]: t["translation"] for t in (rec.get("terminology") or [])
            if isinstance(t, dict) and t.get("term") and t.get("translation")}


def harness_score(term_hits: int, term_total: int, complete_paras: int, total_paras: int,
                  qa_errors: int, refused: int) -> float:
    """0-100 score for what the harness (not the model) controls: glossary
    applied (40), every paragraph delivered in the target script (30), QA-clean
    (20), nothing refused/abandoned (10). A perfect harness run reads ~100 even
    when the reference paraphrases and chrF cannot."""
    terms = (term_hits / term_total) if term_total else 1.0
    complete = (complete_paras / total_paras) if total_paras else 1.0
    qa = 1.0 if qa_errors == 0 else max(0.0, 1.0 - qa_errors / max(total_paras, 1))
    clean = 0.0 if refused else 1.0
    return round(40 * terms + 30 * complete + 20 * qa + 10 * clean, 1)


def run(args) -> dict:
    workdir = Path(args.workdir or (DATA_ROOT / "eval_harness_vs_bare"))
    workdir.mkdir(parents=True, exist_ok=True)
    for stale in workdir.glob("case*.db*"):  # reruns must not inherit rows from earlier runs
        stale.unlink()
    provider = service.make_provider(args.provider, args.model, args.base_url)
    helper = service.helper_for(provider, args.provider, args.base_url, args.helper_model or "")
    print(f"model={provider.name} helper={helper.name}")
    records = load_records(args)[:args.samples]
    print(f"samples={len(records)}")

    rows = []
    for i, rec in enumerate(records):
        src_lang, tgt_lang = rec["source_language"], rec["target_language"]
        paras = split_paras(rec["source"], args.paras)
        ref = align_reference_window(rec["source"], "\n\n".join(paras), rec["reference"])

        # ---- (1) bare model -------------------------------------------------
        t0 = time.time()
        try:
            bare = provider.translate("\n\n".join(paras), source_lang=src_lang, target_lang=tgt_lang)
            bare_err = ""
        except Exception as e:  # noqa: BLE001
            bare, bare_err = "", f"{type(e).__name__}: {e}"[:200]
        bare_s = time.time() - t0

        # ---- (2) full harness -----------------------------------------------
        t0 = time.time()
        db = Database(workdir / f"case{i}.db")
        pid = db.create_project(f"case{i}", source_lang=src_lang, target_lang=tgt_lang)
        doc = db.add_document(pid, rec["id"], "eval", rec["id"])
        ch_ids = store_chapters(db, pid, doc, [ParsedChapter(rec["id"], to_html(paras), paras)])
        analyze_project(db, pid, provider=None, term_provider=None if args.provider == "mock" else helper)        # Gold terminology (e.g. OpenSakura's per-row glossary) is seeded as
        # confirmed terms: the harness must APPLY it, bare never sees it.
        # Without this the comparison can't measure glossary use at all.
        for g in gold_glossary(rec).items():
            from lorebridge.models import TerminologyEntry

            db.add_terminology(TerminologyEntry(None, pid, [g[0]], {tgt_lang: g[1]}, "", "preferred", "confirmed", 99))
        n = translate_project(db, pid, provider, project_dir=None, chapter_id=ch_ids[0], candidates=args.candidates)
        segs = db.segments_for_chapter(ch_ids[0])
        harness = "\n\n".join(s["translated_text"] or "" for s in segs)
        harness_s = time.time() - t0
        glossary = build_glossary(db, pid)
        in_src = {k: v for k, v in glossary.items() if k in rec["source"]}
        refused = sum(1 for s in segs if s["state"] == SegmentState.NEEDS_REVIEW.value and not s["translated_text"])
        qa_errors = sum(1 for sev, _, _ in check_segment(
            "\n".join(s["source_text"] for s in segs), harness, src_lang, glossary) if sev == "error")
        db.close()

        gloss = gold_glossary(rec) or in_src  # opensakura: gold; else the harness's own glossary
        excerpt = "\n\n".join(paras)  # score what was actually translated, not the whole chapter
        bb, bt = term_adherence(excerpt, bare, gloss)
        hb, ht = term_adherence(excerpt, harness, gloss)

        def complete(text: str) -> int:
            n = 0
            for p in text.split("\n\n"):
                if not p.strip():
                    continue
                if tgt_lang == "en" and _CJK.search(p):
                    continue
                n += 1
            return n

        bare_qa_errors = sum(1 for sev, _, _ in check_segment(excerpt, bare, src_lang, {}) if sev == "error")
        row = {
            "id": rec["id"][:60], "paras": len(paras), "segments": n,
            "bare_chrf": round(chrf(bare, ref), 2) if bare else 0.0,
            "harness_chrf": round(chrf(harness, ref), 2) if harness else 0.0,
            "bare_terms": f"{bb}/{bt}", "harness_terms": f"{hb}/{ht}",
            "bare_cjk_left": sum(1 for p in bare.split("\n\n") if _CJK.search(p)) if tgt_lang == "en" else 0,
            "harness_cjk_left": sum(1 for h in harness.split("\n\n") if _CJK.search(h)) if tgt_lang == "en" else 0,
            "bare_seconds": round(bare_s, 1), "harness_seconds": round(harness_s, 1),
            "refused": refused, "bare_error": bare_err,
            "glossary_terms": len(in_src),
            "bare_score": harness_score(bb, bt, complete(bare), len(paras), bare_qa_errors, 1 if bare_err else 0),
            "harness_score": harness_score(hb, ht, complete(harness), len(paras), qa_errors, refused),
        }
        rows.append(row)
        print(f"[{i+1}/{len(records)}] bare chrF={row['bare_chrf']} harness chrF={row['harness_chrf']} "
              f"terms bare={row['bare_terms']} harness={row['harness_terms']} "
              f"({row['bare_seconds']}s vs {row['harness_seconds']}s)")
    return {"model": provider.name, "helper": helper.name, "dataset": args.dataset or args.local,
            "samples": len(records), "candidates": args.candidates, "rows": rows}


def report(result: dict, path: str) -> Path:
    rows = result["rows"]
    n = max(len(rows), 1)
    mean = lambda k: round(sum(r[k] for r in rows) / n, 2)
    L = [f"# Bare model vs harness: {result['dataset']}", "",
         f"Model `{result['model']}` + helper `{result['helper']}` · {result['samples']} samples" + (f" · {result.get('candidates', 1)} candidates" if result.get('candidates', 1) > 1 else "") + ".",
         "chrF++ vs the reference (higher = closer). Term adherence = glossary terms in the translated excerpt rendered as agreed "
         "(gold glossary when the dataset ships one, else the harness's own mined glossary).", "",
         "| # | chrF bare | chrF harness | terms bare | terms harness | Harness Score b/h | CJK-left b/h | time b/h |",
         "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        L.append(f"| {i} | {r['bare_chrf']} | {r['harness_chrf']} | {r['bare_terms']} | {r['harness_terms']} | "
                 f"{r['bare_score']}/{r['harness_score']} | "
                 f"{r['bare_cjk_left']}/{r['harness_cjk_left']} | {r['bare_seconds']}s/{r['harness_seconds']}s |")
    L += ["", f"**Mean chrF**: bare {mean('bare_chrf')} vs harness {mean('harness_chrf')} "
             f"(Δ {round(mean('harness_chrf') - mean('bare_chrf'), 2)})",
          f"**Mean Harness Score**: bare {mean('bare_score')} vs harness {mean('harness_score')} "
          f"(terms applied + complete delivery + QA-clean + nothing refused)", ""]
    tb = sum(int(r["bare_terms"].split("/")[0]) for r in rows)
    th = sum(int(r["harness_terms"].split("/")[0]) for r in rows)
    tt = sum(int(r["bare_terms"].split("/")[1]) for r in rows)
    L.append(f"**Term adherence total**: bare {tb}/{tt} vs harness {th}/{tt}")
    L.append("")
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="parallel-fiction", help="parallel-fiction|opensakura")
    ap.add_argument("--local", default="", help="local JSONL file instead of Hub")
    ap.add_argument("--split", default="", help="Hub split, e.g. 'test[:10]' (default per adapter)")
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--paras", type=int, default=12, help="paragraphs per sample taken from the chapter head (0 = whole chapter)")
    ap.add_argument("--candidates", type=int, default=1, help="model draws per paragraph in the harness arm")
    ap.add_argument("--provider", default="hymt")
    ap.add_argument("--model", default="")
    ap.add_argument("--helper-model", default="")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--workdir", default="")
    ap.add_argument("--out", default="eval_runs/harness_vs_bare.md")
    ap.add_argument("--json", default="eval_runs/harness_vs_bare.json")
    args = ap.parse_args()
    if not args.split:
        args.split = "test[:20]" if args.dataset == "opensakura" else "train[:20]"
    if not args.model:
        args.model = service.DEFAULT_TRANSLATE_MODEL if args.provider == "hymt" else service.DEFAULT_HELPER_MODEL
    result = run(args)
    print(report(result, args.out))
    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
