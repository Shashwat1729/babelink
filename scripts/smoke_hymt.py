"""Hy-MT2 smoke test: a few zh/ja lines, with and without glossary.
Usage: python scripts/smoke_hymt.py [--model ...]
"""
import sys
import time
from pathlib import Path

for stream in (sys.stdout, sys.stderr):  # legacy Windows console (cp1252) cannot print CJK
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lorebridge import service

model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else service.DEFAULT_TRANSLATE_MODEL
p = service.make_provider("hymt", model)
print("provider:", p.name, "| mt_only:", service.is_mt_only(p))
print("helper:", service.helper_for(p, "hymt", "").name)

cases = [
    ("zh", "en", "林凡走进青云宗，大师兄对他点了点头。", {"林凡": "Lin Fan", "青云宗": "Azure Cloud Sect"}),
    ("zh", "en", "“快撤！”他大喝一声，众人纷纷向山下跑去。", {}),
    ("ja", "en", "翌朝、俺は朝日の中目覚めた。扉がノックされた。", {}),
]
for sl, tl, text, glo in cases:
    t0 = time.time()
    out = p.translate(text, glossary=glo, source_lang=sl, target_lang=tl)
    print(f"[{sl}->{tl} {time.time()-t0:.1f}s] {text[:40]!r}\n  -> {out!r}")
print("SMOKE OK")
