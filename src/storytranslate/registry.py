"""Model registry: metadata per model (spec §7)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ModelInfo:
    name: str
    provider: str  # ollama | openai-compatible | ...
    backend: str  # ollama | llama.cpp | vllm | ...
    languages: list[str]
    capabilities: dict[str, bool]  # translation, literary_editing, reasoning, ner, long_context
    recommended_tasks: list[str]
    ram_gb: float = 0
    vram_gb: float = 0
    quantizations: list[str] = field(default_factory=list)


# Small starter registry; user can extend via models.json in project dir.
DEFAULT_REGISTRY: list[ModelInfo] = [
    ModelInfo("qwen2.5:7b", "ollama", "ollama", ["zh", "en", "ja", "ko"], {"translation": True, "literary_editing": True, "reasoning": True}, ["translation", "analysis"], ram_gb=8, vram_gb=6, quantizations=["Q4_K_M"]),
    ModelInfo("qwen2.5:14b", "ollama", "ollama", ["zh", "en", "ja", "ko"], {"translation": True, "literary_editing": True, "reasoning": True, "long_context": True}, ["translation", "refinement", "analysis"], ram_gb=16, vram_gb=12, quantizations=["Q4_K_M"]),
    ModelInfo("aya:8b", "ollama", "ollama", ["en", "hi", "es", "fr", "de", "zh"], {"translation": True}, ["translation"], ram_gb=8, vram_gb=6, quantizations=["Q4_K_M"]),
    ModelInfo("nllb-200", "transformers", "transformers", ["zh", "en", "hi", "es", "ja", "ko", "fr", "de"], {"translation": True, "ner": False}, ["translation"], ram_gb=4, vram_gb=2, quantizations=[]),
    ModelInfo("huihui_ai/qwen3-abliterated:4b", "ollama", "ollama", ["zh", "en", "ja", "ko"], {"translation": True, "literary_editing": True, "reasoning": True}, ["translation", "analysis"], ram_gb=4, vram_gb=0, quantizations=["Q4_K_M"]),
    ModelInfo("huihui_ai/qwen3-abliterated:latest", "ollama", "ollama", ["zh", "en", "ja", "ko"], {"translation": True, "literary_editing": True, "reasoning": True, "long_context": True}, ["translation", "refinement"], ram_gb=8, vram_gb=6, quantizations=["Q4_K_M"]),
    ModelInfo("huihui_ai/gemma3-abliterated:4b", "ollama", "ollama", ["en", "zh", "ja", "ko", "hi", "es", "fr", "de"], {"translation": True, "literary_editing": True}, ["translation", "refinement"], ram_gb=4, vram_gb=0, quantizations=["Q4_K_M"]),
    ModelInfo("muse-3b-4k", "ollama", "ollama", ["en"], {"reasoning": True}, ["qa", "analysis"], ram_gb=4, vram_gb=0, quantizations=[]),
]


def recommend(target_lang: str, vram_gb: float, ram_gb: float, quality: str = "balanced") -> list[ModelInfo]:
    """Pick candidates that fit and support the target language."""
    out = []
    for m in DEFAULT_REGISTRY:
        if target_lang not in m.languages:
            continue
        if m.vram_gb > vram_gb and m.ram_gb > ram_gb:
            continue
        out.append(m)
    if quality == "fast":
        out.sort(key=lambda m: m.ram_gb)
    elif quality in ("quality", "max"):
        out.sort(key=lambda m: -m.ram_gb)
    else:
        out.sort(key=lambda m: abs(m.ram_gb - 8))
    return out
