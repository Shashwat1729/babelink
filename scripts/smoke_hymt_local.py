"""Hy-MT2 direct-HF smoke test (transformers, CPU bf16, weights on D:).
Usage: python scripts/smoke_hymt_local.py
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HOME", r"D:\StoryTranslation_data\hf")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from babelink import service  # noqa: E402

t0 = time.time()
p = service.make_provider("hymt-local", "tencent/Hy-MT2-1.8B")
print(f"provider: {p.name} | mt_only: {service.is_mt_only(p)}", flush=True)

cases = [
    ("zh", "en", "林凡走进青云宗，大师兄对他点了点头。", {"林凡": "Lin Fan", "青云宗": "Azure Cloud Sect"}),
    ("zh", "en", "“快撤！”他大喝一声，众人纷纷向山下跑去。", {}),
    ("ja", "en", "翌朝、俺は朝日の中目覚めた。扉がノックされた。", {}),
]
for sl, tl, text, glo in cases:
    t1 = time.time()
    out = p.translate(text, glossary=glo, source_lang=sl, target_lang=tl)
    print(f"[{sl}->{tl} {time.time()-t1:.1f}s] {text[:40]!r}\n  -> {out!r}", flush=True)
print(f"SMOKE-LOCAL OK ({time.time()-t0:.0f}s total)")
