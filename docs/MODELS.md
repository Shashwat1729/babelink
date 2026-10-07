# Models

## Local-first principle

No commercial API is mandatory. Everything can run locally via Ollama, llama.cpp,
vLLM, or any OpenAI-compatible endpoint.

## Registry

`storytranslate models --target-lang en` prints hardware-aware recommendations.
The starter registry in `src/storytranslate/registry.py` ships a few known models;
extend it as you add models.

## Abliterated models

Fiction often contains mature or disturbing content. Standard instruct models may
refuse ordinary translation. Use abliterated (refusal-removed) local variants, e.g.:

```bash
ollama pull huihui_ai/qwen3-abliterated
```

These are uncensored Qwen builds from Huihui-ai. Use a normal instruct model if
your content does not trigger refusals.

## Per-task routing (spec §9)

- **NER / small jobs** — a 3B local model (e.g. muse-3b-4k, local-fallback-4k)
- **Terminology extraction** — small multilingual model
- **Translation** — specialized or strong 7B/14B (qwen2.5:14b where VRAM allows)
- **Refinement** — same or stronger model
- **QA** — small model + deterministic checks

Confidence escalation (spec §10) is on the roadmap.
