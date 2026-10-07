"""Lightweight benchmark: time a single translation call."""
from __future__ import annotations

import time

from .providers.base import LLMProvider

SAMPLE = "林凡修炼到了炼气期。他突破了，感觉到了前所未有的力量。"


def benchmark(provider: LLMProvider) -> dict:
    start = time.perf_counter()
    out = provider.translate(SAMPLE, glossary={})
    elapsed = time.perf_counter() - start
    return {"model": provider.name, "chars": len(out), "seconds": round(elapsed, 2), "sample_output": out[:200]}
