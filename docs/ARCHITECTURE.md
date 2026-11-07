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
babelink/
├── apps/cli            # `babelink` headless entry (Phase 1)
├── apps/desktop        # PySide6 (later)
├── core/               # projects, documents, segmentation, entities,
│                       # terminology, story_bible, translation_memory,
│                       # context, translation, refinement, qa, evaluation, export
├── models/             # registry, hardware detection, routing, benchmarking
├── providers/          # llama_cpp, ollama, vllm, transformers, openai_compatible
├── importers/ epub txt docx html   # comic pages: images.py
├── exporters/ epub html txt bilingual
├── profiles/ languages genres styles
├── datasets/ manifests evaluation regression
├── tests/ unit integration regression evaluation
├── docs/
└── scripts/
```

## Implemented layout

The tree above is the target from spec §94. The repository today is a single package: `src/babelink/` with `service.py` (operations shared by CLI and web),
`cli.py`, `web.py` + `static/`, `config.py`, `pipeline.py`, `context.py`, `terminology.py`, `story_bible.py`, `qa.py`, `refinement.py`, `review.py`,
`importers/`, `exporters/`, `providers/`, `images.py` (comic pages, optional `[images]` extra) and the evaluation modules. See DEVELOPMENT.md.

## Story Graph (2026-10-14 overhaul)

`src/babelink/graph.py` (new) is the narrative knowledge-graph layer over the
Story Bible. `story_bible.py` keeps its public API but normalizes onto the graph:
entity sections stay lists of dicts, now with stable `type:name` IDs
(`character:贾政`), typed edges in `relationships` (`member_of, allied_with,
located_in, …`, plus `appears/active_in_chapter` and `co_occurs`), `first_seen`
chapter numbers, and alias merging on upsert. `link_recommendations` proposes
co-occurrence edges over shared chapters; nothing is auto-applied — a human
or the review step approves edges. Unknown entity/edge fields are preserved
through load/save.

Consistency machinery on top of the graph (terminology.py, entities.py):
list-free distinctiveness (burstiness + rendering over-spread demote diffuse
agreements to vote-only), co-occurrence sense splitting (`canon["senses"]`
keeps two distinct winners per term), and a first-mention anchor as a prior.

Retrieval (context.py) is entity-aware: literal term presence + alias sharing
rank past segments, bible summaries ride along, and the render budget is exact
(every item measured including prefixes and separators).

QA (qa.py, escalation.py, estimate.py) now covers term-variant leak (losing
surfaces from elections), mechanical voice-register drift (IQR outlier, fail-open
under 8 baseline segments), and antecedent checks (closed-class grammar only).
Round-trip chrF is validated as a router (fixture AUC 0.988) and threaded with
the losing surfaces through `translate_project` into both draft selection and
post-hoc QA.

Providers (fallback.py, providers/): `keep_alive` constructor kwarg, warm-up
helpers, memoized providers (usage counters reset per reuse), join-batch
`translate_batch` (additive, unused by the pipeline). `translate_project`
warms the provider once before the segment loop (best-effort).

Comic pages (images.py): `order_blocks` (RTL columns for vertical manga, rows
otherwise), two-pass low-confidence re-OCR (2× upscale, keeps the higher
score), and typeset overflow flags (`Block.needs_review`) instead of clipped
ink outside the inscribed area.

## Key Concepts

- **Project**: one book/series. Owns Story Bible, terminology, TM, style guide, segment states.
- **Segment**: chapter → scene → paragraph-sized unit; state machine per spec §40.
- **Story Bible**: canonical entities with language-independent IDs; versioned JSON files + SQLite mirror; contradictions flagged (ADR-004).
- **Story Graph**: typed entities + typed edges over the Bible (`graph.py`); `first_seen`, chapter presence, co-occurrence suggestions with human review.
- **Terminology**: canonical terms with policy (`fixed|preferred|flexible|contextual|...`), distinct from flexible prose vocabulary.
- **Translation Memory**: semantic + exact recall; reference, never blind substitution.
- **Context assembly**: ranked by importance/relevance/entity overlap/recency/similarity within a token budget (§80).
- **Model routing**: task→model map + escalation ladder (§9, §10, ADR-005).
- **Provenance**: every segment records model, prompt version, bible/terminology/TM versions, settings (§65).

## Comic pages

```
page image / CBZ -> tile tall strips -> RapidOCR lines -> group lines into bubbles -> reading order
     -> one chapter per page, one segment per bubble (`regions` table keeps pixel boxes and OCR line quads)
     -> the same translate / refine / QA / TM / glossary pipeline as text
     -> erase old lettering (ring-colour fill or inpaint) -> typeset the translation in the bubble -> PNG / CBZ
```

A page is a chapter whose `documents.format` is `image`. Everything after OCR is the text pipeline, so the glossary, translation memory and
context carry across pages exactly as they do across chapters. `images.py` is imported lazily; see ADR-013.

## Jobs and live output

One job runs per project (a second request gets 409). `web.py` runs it on a thread and keeps `Job` state: progress, a word count, the paragraph being written (`live`, with the text so far) and the last few finished paragraphs (`recent`). The page polls it, reloads the lists when a paragraph finishes and writes the streamed text straight into the paragraph card. Providers stream through an optional `on_delta` callback; the pipeline turns it into `on_text` events and checks the stop flag inside it, so Stop ends the paragraph being written. Job writes are conditional on the segment state, and requests that change state carry an `Idempotency-Key` (ADR-015).

## Persistence

SQLite (WAL) as system of record; files under `story_bible/`, `summaries/` are user-editable views. Vector index is a sidecar, optional for small projects (ADR-003).

## Phase-1 Scope (MVP)

From spec §98: EPUB/TXT import → language detect → chapters → initial story bible → terminology → local-model recommendation → context-aware translation → terminology enforcement → deterministic+AI QA → human edit → resume → translated EPUB export. Comic pages came later (ADR-013); no desktop UI, no benchmarking UI.

## Non-goals / Anti-patterns

- No mega-prompt translator.
- Comic-page OCR and typesetting stay behind the optional `[images]` extra; the text pipeline never imports them.
- No silent terminology merges or bible overwrites.
- No cloud-mandatory flow.
- No bundling of datasets with unclear licenses.
