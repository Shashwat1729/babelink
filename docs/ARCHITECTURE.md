# Architecture

Source of truth: `MASTER_PROJECT_SPEC.md`. Read it first.

## Design Principle

The unit of translation is the **story segment within a persistent world**, not the sentence.
Every stage is a replaceable module behind a small interface (spec §71).

## Pipeline Stages (spec §28)

```
import → language detect → parse → chapters → scenes → entities →
terminology → bible/TM/context retrieval → context assembly →
first-pass translate → refinement → deterministic QA → AI QA →
human review → export
```

Each stage: pure function over typed artifacts where possible, persisted between runs for crash safety (spec §41), and individually re-runnable.

## Repository Layout (from spec §94, adapted)

```
story-translate/
├── apps/cli            # `storytranslate` headless entry (Phase 1)
├── apps/desktop        # PySide6 (later)
├── core/               # projects, documents, segmentation, entities,
│                       # terminology, story_bible, translation_memory,
│                       # context, translation, refinement, qa, evaluation, export
├── models/             # registry, hardware detection, routing, benchmarking
├── providers/          # llama_cpp, ollama, vllm, transformers, openai_compatible
├── importers/ epub txt docx html manga
├── exporters/ epub html txt bilingual
├── profiles/ languages genres styles
├── datasets/ manifests evaluation regression
├── tests/ unit integration regression evaluation
├── docs/
└── scripts/
```

## Key Concepts

- **Project**: one book/series. Owns Story Bible, terminology, TM, style guide, segment states.
- **Segment**: chapter → scene → paragraph-sized unit; state machine per spec §40.
- **Story Bible**: canonical entities with language-independent IDs; versioned JSON files + SQLite mirror; contradictions flagged (ADR-004).
- **Terminology**: canonical terms with policy (`fixed|preferred|flexible|contextual|...`), distinct from flexible prose vocabulary.
- **Translation Memory**: semantic + exact recall; reference, never blind substitution.
- **Context assembly**: ranked by importance/relevance/entity overlap/recency/similarity within a token budget (§80).
- **Model routing**: task→model map + escalation ladder (§9, §10, ADR-005).
- **Provenance**: every segment records model, prompt version, bible/terminology/TM versions, settings (§65).

## Persistence

SQLite (WAL) as system of record; files under `story_bible/`, `summaries/` are user-editable views. Vector index is a sidecar, optional for small projects (ADR-003).

## Phase-1 Scope (MVP)

From spec §98: EPUB/TXT import → language detect → chapters → initial story bible → terminology → local-model recommendation → context-aware translation → terminology enforcement → deterministic+AI QA → human edit → resume → translated EPUB export. No manga, no desktop UI, no benchmarking UI in Phase 1.

## Non-goals / Anti-patterns

- No mega-prompt translator.
- No manga pipeline in core.
- No silent terminology merges or bible overwrites.
- No cloud-mandatory flow.
- No bundling of datasets with unclear licenses.
