# Story Translate

[![tests](https://img.shields.io/badge/tests-14%20passed-brightgreen)]()
[![python](https://img.shields.io/badge/python-3.11%2B-blue)]()
[![license](https://img.shields.io/badge/license-TBD-yellow)]()

**Context-aware, consistent, multilingual, long-form story translation with local-first AI and measurable quality.**

Not a sentence-by-sentence machine translator. Story Translate treats the unit of translation as a *story segment interpreted within a persistent world*: Story Bible, canonical terminology, translation memory, hierarchical context, model routing, a second literary pass, and rigorous QA.

## Why

Long-form fiction fails on vanilla MT because of terminology drift, inconsistent names, wrong pronouns/speakers, literal prose, and broken dialogue. This project attacks those failure modes structurally.

## Features (current)

- **EPUB / TXT import** with chapter + paragraph segmentation
- **Language detection** (CJK-aware + langdetect)
- **Story Bible** — user-editable JSON sections (characters, locations, orgs, terms, timeline, style, summaries, contradictions)
- **Terminology mining** — recurring CJK n-grams + capitalized phrases, progressive suggestions, drift detection
- **Translation Memory** + glossary-aware prompts
- **Hybrid context retrieval** — entity overlap + recency + summary within a char budget
- **Two-pass translation** — first-pass MTL, then guarded literary refinement (canonical terms / numbers / length checks)
- **Deterministic QA** (missing translation, CJK leakage, numbers, omissions) + **AI QA** structured findings
- **Selective retranslation** — term change marks affected segments STALE and retranslates only those
- **Style presets** — literal / faithful-natural / literary / highly-literary / custom
- **Model registry + recommendation** — hardware detection, per-pair fit, quality/fast/balanced ranking
- **Providers** — Ollama, any OpenAI-compatible endpoint, deterministic mock for CI
- **Evaluation** — JSONL dataset adapter, char-F1 runner, markdown leaderboard
- **Local-first** — no cloud account required; Ollama + local GGUF endpoints work out of the box

## Install

```bash
pip install -e .
storytranslate --help
```

Requires Python 3.11+. For local inference, install [Ollama](https://ollama.com) and pull a model:

```bash
ollama pull huihui_ai/qwen3-abliterated
```

## Quickstart

```bash
storytranslate --project-dir demo import novel.epub
storytranslate --project-dir demo analyze 1
storytranslate --project-dir demo translate 1 --provider ollama --model huihui_ai/qwen3-abliterated
storytranslate --project-dir demo refine 1 --provider ollama --model huihui_ai/qwen3-abliterated
storytranslate --project-dir demo review 1 --provider ollama --model huihui_ai/qwen3-abliterated
storytranslate --project-dir demo qa 1
storytranslate --project-dir demo export 1 --source novel.epub --out translated.epub
```

Other useful commands:

```bash
storytranslate models --target-lang en            # hardware-aware model recommendation
storytranslate benchmark --provider ollama --model huihui_ai/qwen3-abliterated
storytranslate benchmark-run ds.jsonl --provider mock --out leaderboard.md
storytranslate storybible                         # list Bible sections
storytranslate style literary                     # set style preset
storytranslate retranslate 1 --term 炼气期 --new "Qi Condensation Stage" --provider ollama
```

## How it works

```
EPUB/TXT -> parse -> chapters -> segments
        -> language detection -> terminology mining -> Story Bible + summaries
        -> context retrieval (Bible terms, chapter summary, recent segments)
        -> first-pass translation (glossary + context + TM)
        -> guarded literary refinement
        -> deterministic QA + AI QA
        -> human edit -> resume (checkpointed per segment)
        -> translated EPUB / TXT
```

Every segment records a state machine: `UNPROCESSED → ANALYZED → TRANSLATED → REFINED → QA_PASSED / NEEDS_REVIEW / HUMAN_APPROVED / STALE`, and every run records model, prompt version, and Bible/terminology/TM versions for reproducibility.

## Roadmap

| Phase | Status |
|---|---|
| 1 — Core (EPUB/TXT, local LLM, glossary, TM, resume, EPUB output) | ✅ |
| 2 — Story Intelligence (Bible, entities, terminology, summaries, context) | ✅ (LLM entity resolution pending) |
| 3 — Quality (two-pass, refinement, AI QA, selective retranslation) | ✅ |
| 4 — Local ML (hardware, registry, recommendation, benchmark) | ✅ (routing/escalation pending) |
| 5 — Evaluation (dataset adapters, benchmark runner, leaderboard) | ✅ (HF/OPUS/WMT/FLORES adapters pending) |
| 6 — Comics (manga/manhua OCR pipeline) | planned |
| Desktop UI (PySide6) | planned |

## Repository layout

```
src/storytranslate/    core package (pipeline, db, providers, importers, exporters, qa, eval)
tests/                 pytest suite (14 tests)
docs/                  MASTER_PROJECT_SPEC.md, ARCHITECTURE.md, ROADMAP.md, DECISIONS.md
```

## License & attribution

TBD before first public release. Reference projects studied (all AGPL-3.0 where applicable) belong to their respective authors; no code was copied.
