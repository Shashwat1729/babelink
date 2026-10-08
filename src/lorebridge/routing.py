"""Per-task model routing presets (spec §9)."""
from __future__ import annotations

PRESETS: dict[str, dict[str, str]] = {
    "fast": {
        "translation": "muse-3b-4k",
        "analysis": "muse-3b-4k",
        "refinement": "muse-3b-4k",
        "qa": "muse-3b-4k",
    },
    "balanced": {
        "translation": "qwen2.5:7b",
        "analysis": "muse-3b-4k",
        "refinement": "qwen2.5:7b",
        "qa": "muse-3b-4k",
    },
    "quality": {
        "translation": "qwen2.5:14b",
        "analysis": "qwen2.5:7b",
        "refinement": "qwen2.5:14b",
        "qa": "qwen2.5:7b",
    },
    "max": {
        "translation": "qwen2.5:32b",
        "analysis": "qwen2.5:14b",
        "refinement": "qwen2.5:32b",
        "qa": "qwen2.5:14b",
    },
}


def route(task: str, preset: str = "balanced") -> str:
    return PRESETS.get(preset, PRESETS["balanced"]).get(task, PRESETS["balanced"]["translation"])
