# Models

## Local-first principle

No commercial API is mandatory. Everything can run locally via Ollama, llama.cpp,
vLLM, or any OpenAI-compatible endpoint.

## Registry

`lorebridge models --target-lang en` prints hardware-aware recommendations.
The starter registry in `src/lorebridge/registry.py` ships a few known models;
extend it as you add models.

## Abliterated models

Fiction often contains mature or disturbing content. Standard instruct models may
refuse ordinary translation. Use abliterated (refusal-removed) local variants, e.g.:

```bash
ollama pull huihui_ai/qwen3-abliterated:4b   # verified working on CPU-only 16GB RAM
```

These are uncensored Qwen builds from Huihui-ai. Use a normal instruct model if
your content does not trigger refusals.

## Per-task routing (spec §9)

- **NER / small jobs** — a 3B local model (e.g. muse-3b-4k, local-fallback-4k)
- **Terminology extraction** — small multilingual model
- **Translation** — specialized or strong 7B/14B (qwen2.5:14b where VRAM allows)
- **Refinement** — same or stronger model
- **QA** — small model + deterministic checks

Configure a model per task in `lorebridge.yaml` (`models: {translate: ..., refine: ...}`).

## Escalation (spec §10)

`translate --escalate-model qwen2.5:14b` (or `escalate_model:` in the config) re-runs risky segments on a stronger Ollama model.
Risk signals are deterministic (`escalation.py`): QA failures, number mismatches or dropped numbers, very long or very short source, dense terminology.

## Ollama notes

- Qwen3-family models are called with `think: false`; otherwise a CPU run spends 20-100 s thinking per paragraph (see REGRESSIONS.md).
  Servers that reject the field are retried without it.
- Measured here: `huihui_ai/qwen3-abliterated:4b` on a CPU-only laptop generates about 9 tokens/s warm; the first call after loading takes about 45 s.
  Plan evaluation runs accordingly (about 8 minutes for 8 paragraphs per configuration).
- `OllamaProvider` keeps the model loaded for 30 minutes (`keep_alive`) and uses an 8192-token context.
