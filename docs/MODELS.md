# Models

## Local-first principle

No commercial API is mandatory. Everything can run locally via Ollama, llama.cpp,
vLLM, or any OpenAI-compatible endpoint.

## Default engine: Tencent Hy-MT2 (translation-specialized)

Translation runs on **Hy-MT2-1.8B** (a local `hymt2-1.8b` model built from the
official Q4_K_M GGUF, ~1.1GB via Ollama) — a translation-only model that
out-translates general 4B chat LLMs on zh/ja→en fiction while running on CPU:

```bash
ollama pull hf.co/tencent/Hy-MT2-1.8B-GGUF:Q4_K_M
ollama create hymt2-1.8b -f Modelfile.hymt2   # installs the official chat template
babelink translate --provider hymt   # --model defaults to hymt2-1.8b
```

Notes that cost real debugging time:

- Hy-MT2 needs its **official prompt format** (terminology block, background
  block, no system prompt) **and** its exact chat template in Ollama
  (`Modelfile.hymt2`). The template's control tokens must be emitted by
  Ollama's template engine: sending them as raw text BPE-fragments them and
  produces degenerate output (`[[[[…`, tool-call JSON, assistant chatter).
  `HymtOllamaProvider` handles both; do not point a generic provider at this
  model.
- Hy-MT2 is **translation-only**: term classification, refinement, review and
  summaries are routed to a general-LLM helper (`--helper-model`, default
  `huihui_ai/qwen3-abliterated:4b`). A 7B GGUF (`Hy-MT2-7B-GGUF:Q4_K_M`) is in
  the registry for stronger hardware.
- Direct-weights fallback: `--provider hymt-local` runs `tencent/Hy-MT2-1.8B`
  via transformers (weights under `HF_HOME`, CPU bf16 ≈ 3.6GB RAM).

## Registry

`babelink models --target-lang en` prints hardware-aware recommendations
(translation-specialized models rank first for translation).
The starter registry in `src/babelink/registry.py` ships a few known models;
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

- **Translation** — Hy-MT2-1.8B (default), Hy-MT2-7B where RAM allows
- **Terminology classification / NER / small jobs** — a general 3-4B local model (e.g. huihui_ai/qwen3-abliterated:4b, muse-3b-4k)
- **Refinement** — same or stronger general model (never the MT model)
- **QA** — small model + deterministic checks

Configure a model per task in `babelink.yaml` (`models: {translate: ..., refine: ...}`).

## Escalation (spec §10)

`translate --escalate-model qwen2.5:14b` (or `escalate_model:` in the config) re-runs risky segments on a stronger Ollama model.
Risk signals are deterministic (`escalation.py`): QA failures, number mismatches or dropped numbers, very long or very short source, dense terminology.

## Ollama notes

- Qwen3-family models are called with `think: false`; otherwise a CPU run spends 20-100 s thinking per paragraph (see REGRESSIONS.md).
  Servers that reject the field are retried without it.
- Measured here: `huihui_ai/qwen3-abliterated:4b` on a CPU-only laptop generates about 9 tokens/s warm; the first call after loading takes about 45 s.
  Plan evaluation runs accordingly (about 8 minutes for 8 paragraphs per configuration).
- `OllamaProvider` keeps the model loaded for 30 minutes (`keep_alive`) and uses an 8192-token context.
