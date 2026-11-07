"""Per-segment latency bench against local Ollama (<=20 inference calls).

Measures: cold (first call after explicit unload), warm (resident), helper
interleaved (engine/helper alternation), and batch-vs-sequential throughput —
using the same provider construction the app uses (service.make_provider /
service.helper_for, keep_alive 30m, num_ctx 4096, MT temperature 0.2).

Usage:
    python scripts/bench_latency.py [--base-url http://localhost:11434]
        [--engine-model hymt2-1.8b] [--helper-model huihui_ai/qwen3-abliterated:4b]
        [--skip-unload] [--segments 3]

When the requested engine/helper models are not installed locally, the bench
falls back to whatever Ollama has (e.g. qwen2.5:3b-instruct) and says so in
the output header. Unloads via keep_alive=0 (no inference cost). Total
inference calls are capped well under the 20-call budget.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx  # noqa: E402

from babelink import service  # noqa: E402
from babelink.fallback import ChainProvider  # noqa: E402
from babelink.providers.ollama import installed  # noqa: E402


# Small fiction-like segments (short, comparable to pipeline segments).
SEGMENTS = [
    "He opened the old wooden door and stepped into the dark room.",
    "The wind pushed the curtains against the window.",
    "She whispered his name, though no one answered.",
    "Rain tapped against the roof all night long.",
]


def unload_model(model: str, base_url: str) -> None:
    """Force-unload so the next call pays real model-load (cold) cost."""
    try:
        httpx.post(f"{base_url.rstrip('/')}/api/generate",
                   json={"model": model, "keep_alive": 0, "prompt": ""}, timeout=15)
    except Exception as e:
        print(f"  [unload {model}: {e}]")


def timed(fn):
    t0 = time.time()
    out = fn()
    return out, time.time() - t0


def _resolve(installed_names: list[str], wanted: str, fallback_order: list[str]) -> str:
    if wanted and wanted in installed_names:
        return wanted
    for c in fallback_order:
        if c in installed_names:
            return c
    return wanted or (installed_names[0] if installed_names else "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:11434")
    ap.add_argument("--engine-model", default="hymt2-1.8b")
    ap.add_argument("--helper-model", default="huihui_ai/qwen3-abliterated:4b")
    ap.add_argument("--skip-unload", action="store_true", help="don't force-unload before cold phase")
    ap.add_argument("--segments", type=int, default=3, help="segments for cold/warm phases")
    args = ap.parse_args()

    try:
        models = [m["name"] for m in installed(args.base_url)]
    except Exception as e:
        print(f"Ollama unreachable at {args.base_url}: {e}")
        return 1
    print(f"Ollama: {args.base_url}  installed: {', '.join(models) or '(none)'}")

    # Same construction as the app: kind hymt/cat via service.make_provider.
    # hymt/cat kinds hardcode their model when model=="" — pass the resolved
    # model explicitly so a missing default doesn't 404 mid-bench.
    engine_model = _resolve(models, args.engine_model, ["hymt2-1.8b", "cat-translate-1.4b", "qwen2.5:3b-instruct", "qwen2.5:7b-instruct"])
    # Prefer a DISTINCT helper model when available (otherwise the interleave
    # phase can't show load/unload thrash): 3b engine + 7b helper alternation
    # is the honest stand-in for hymt (1.8B) + 4B helper.
    helper_candidates = [c for c in ["huihui_ai/qwen3-abliterated:4b", "qwen2.5:7b-instruct", "qwen2.5:3b-instruct"] if c != engine_model]
    helper_candidates.append(engine_model)  # last resort: same model (no thrash possible)
    helper_model = _resolve(models, args.helper_model, helper_candidates)
    if wanted := args.engine_model:
        if engine_model != wanted:
            print(f"  engine fallback: {wanted!r} not installed -> using {engine_model!r} (stand-in)")
    if helper_model != args.helper_model:
        print(f"  helper fallback: {args.helper_model!r} not installed -> using {helper_model!r} (stand-in)")

    # Distinguish MT-only engine (hymt/cat kind) from generic Ollama engine:
    # generic qwen models serve as both engine+helper, so use kind=ollama.
    engine_kind = "hymt" if "hymt" in engine_model.lower() else ("cat" if "cat" in engine_model.lower() else "ollama")
    engine = service.make_provider(engine_kind, engine_model, args.base_url)
    helper = service.helper_for(engine, engine_kind, args.base_url, helper_model)
    # service.helper_for returns the engine itself for general LLMs (correct
    # app behavior — no alternation). For the THRASH CHECK we still want to
    # model the MT case (engine+helper distinct), so build a synthetic
    # distinct-model helper for the interleave phase only.
    synthetic_helper = None
    if helper is engine and helper_model and helper_model != engine_model:
        from babelink.providers.ollama import OllamaProvider

        synthetic_helper = OllamaProvider(model=helper_model, base_url=args.base_url, num_ctx=4096)
        print(f"  interleave helper (synthetic, models MT alternation): {synthetic_helper.name} num_ctx=4096")
    cfg = {"engine": getattr(engine, "name", "?"), "engine_keep_alive": getattr(engine, "keep_alive", "n/a"),
           "engine_num_ctx": getattr(engine, "num_ctx", "n/a"), "engine_temp": getattr(engine, "temperature", "0.2 (base)"),
           "helper": getattr(helper, "name", "?"), "helper_num_ctx": getattr(helper, "num_ctx", "n/a")}
    print("config: " + " | ".join(f"{k}={v}" for k, v in cfg.items()))
    print(f"chain: {ChainProvider([engine]).name if not getattr(engine, 'translation_only', False) else 'translate[engine+fallback:helper]'} (app builds resolve_chains)")

    segs = SEGMENTS[:max(1, args.segments)]
    src_lang, tgt_lang = ("ja", "en") if engine_kind == "cat" else ("zh", "en")
    # English fixture text is used for all engines (qwen stand-ins are
    # language-agnostic); keep it tiny so CPU latency reflects load+decode.
    rows: list[dict] = []
    calls = 0

    def do_translate(src: str):
        nonlocal calls
        calls += 1
        return engine.translate(src, source_lang=src_lang, target_lang=tgt_lang)

    # --- cold: force unload, then segment 1 pays load; avg over seeds shows it.
    if not args.skip_unload:
        unload_model(engine_model, args.base_url)
        if helper_model and helper_model != engine_model:
            unload_model(helper_model, args.base_url)
    cold_times = []
    for s in segs:
        _, dt = timed(lambda s=s: do_translate(s))
        cold_times.append(dt)
    rows.append({"phase": "cold", "calls": len(cold_times), "avg_s": sum(cold_times) / len(cold_times),
                 "first_s": cold_times[0], "note": "1st call incl. model load"})

    # --- warm: models now resident (keep_alive 30m), steady state.
    warm_times = []
    for s in segs:
        _, dt = timed(lambda s=s: do_translate(s))
        warm_times.append(dt)
    rows.append({"phase": "warm", "calls": len(warm_times), "avg_s": sum(warm_times) / len(warm_times),
                 "first_s": warm_times[0], "note": "resident, no reload expected"})

    # --- interleaved: helper task between engine segments (thrash check).
    inter_times = []
    inter_calls = 0
    inter_helper = synthetic_helper or helper
    for s in segs[:2]:
        _, dt_h = timed(lambda: inter_helper.summarize("A short chapter about a journey.", "en"))
        inter_calls += 1
        _, dt = timed(lambda s=s: do_translate(s))
        inter_times.append(dt)
    rows.append({"phase": "interleaved (helper between)", "calls": 2 * len(inter_times), "avg_s": sum(inter_times) / len(inter_times),
                 "first_s": inter_times[0] if inter_times else 0, "note": f"{len(inter_times)} engine + {inter_calls} helper ({getattr(inter_helper, 'name', '?')}); thrash would spike avg vs warm"})

    # --- batch vs sequential: 4 segs via translate_batch (budgeted groups)
    # vs 4 sequential translates. Counted separately (4 + ~1-4 calls).
    seq_times = []
    for s in SEGMENTS[:4]:
        _, dt = timed(lambda s=s: do_translate(s))
        seq_times.append(dt)
    chain = ChainProvider([engine])
    batch_segs = SEGMENTS[:4]
    # translate_batch group budget is 900 chars: these 4 joined fit one call
    # on a healthy provider; a split-miss degrades to per-item (still correct).
    _, batch_dt = timed(lambda: chain.translate_batch(batch_segs, source_lang=src_lang, target_lang=tgt_lang))
    rows.append({"phase": "batch (4 segs, 1 group)", "calls": 1, "avg_s": batch_dt, "first_s": batch_dt,
                 "note": "single joined call if marker survives, else per-item fallback"})
    rows.append({"phase": "sequential (same 4 segs)", "calls": 4, "avg_s": sum(seq_times) / len(seq_times),
                 "first_s": seq_times[0], "note": f"total seq={sum(seq_times):.2f}s vs batch={batch_dt:.2f}s"})

    print(f"\ninference calls this bench: {calls} engine/helper translates + 1 batch + {inter_calls} helper = {calls + 1 + inter_calls} (budget <=20)")
    print(f"{'phase':<32} {'calls':>5} {'avg_s':>8} {'first_s':>8}  note")
    print("-" * 96)
    for r in rows:
        print(f"{r['phase']:<32} {r['calls']:>5} {r['avg_s']:>8.2f} {r['first_s']:>8.2f}  {r['note']}")
    warm = rows[1]["avg_s"]
    inter = rows[2]["avg_s"]
    print(f"\ninterpretation: cold_first={rows[0]['first_s']:.2f}s warm_avg={warm:.2f}s interleaved_avg={inter:.2f}s "
          f"-> interleaved/warm={inter/warm:.2f}x (near 1.0 = no thrash with keep_alive 30m)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
