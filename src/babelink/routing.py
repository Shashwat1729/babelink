"""Per-task model routing presets (spec §9).

Translation runs on Hy-MT2 (translation-specialized, ~1.1GB); everything else
(term analysis, refinement, QA) needs a general LLM helper.
"""
from __future__ import annotations

from .hymt import DEFAULT_MODEL as HYMT_DEFAULT

HELPER_SMALL = "huihui_ai/qwen3-abliterated:4b"

PRESETS: dict[str, dict[str, str]] = {
    "fast": {
        "translation": HYMT_DEFAULT,
        "analysis": "muse-3b-4k",
        "refinement": "muse-3b-4k",
        "qa": "muse-3b-4k",
    },
    "balanced": {
        "translation": HYMT_DEFAULT,
        "analysis": HELPER_SMALL,
        "refinement": HELPER_SMALL,
        "qa": "muse-3b-4k",
    },
    "quality": {
        "translation": "hf.co/tencent/Hy-MT2-7B-GGUF:Q4_K_M",
        "analysis": "qwen2.5:7b",
        "refinement": "qwen2.5:14b",
        "qa": "qwen2.5:7b",
    },
    "max": {
        "translation": "hf.co/tencent/Hy-MT2-7B-GGUF:Q4_K_M",
        "analysis": "qwen2.5:14b",
        "refinement": "qwen2.5:32b",
        "qa": "qwen2.5:14b",
    },
}


def route(task: str, preset: str = "balanced") -> str:
    return PRESETS.get(preset, PRESETS["balanced"]).get(task, PRESETS["balanced"]["translation"])
