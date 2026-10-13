# Roadmap

Phases follow spec §91. Each phase ends with an evaluation gate (ADR-007): benchmark delta vs
previous phase on fixed dataset slices. Boxes are checked only when the feature exists and has a test
(or, for model-quality claims, a recorded run in [EVALUATION.md](EVALUATION.md)).

## Phase 1 — Core

- [x] Python package, CLI `babelink`
- [x] SQLite (WAL) schema: projects, documents, chapters, segments, terminology, TM, QA, edits, model runs
- [x] EPUB importer/exporter (source EPUB rewritten in place, or rebuilt; bilingual option)
- [x] TXT, HTML/XHTML and pasted-text importers
- [x] Language detection
- [x] Chapter/paragraph segmentation
- [x] Ollama + OpenAI-compatible providers, deterministic mock
- [x] Glossary / terminology store
- [x] Translation memory (exact reuse; project-scope human edits)
- [x] Resumable per-segment checkpointing
- [x] Deterministic QA
- [x] CLI: import / paste / analyze / translate / qa / export / validate
- [x] Export validation (§84)
- [x] YAML configuration (§86)

## Phase 2 — Story Intelligence

- [x] Story Bible (JSON sections, user-editable in the web UI)
- [x] Terminology mining, model-suggested renderings, confirm / edit / ignore
- [x] Chapter summaries
- [x] Hybrid context retrieval, context budget
- [x] Contradiction / drift detection
- [x] Chapter-wise paste that reuses project memory (test: `tests/test_continuity.py`)
- [ ] LLM entity extraction + resolution statuses (entities are user-entered or mined terms today)
- [ ] Relationships / timeline tracking

## Phase 3 — Quality

- [x] Two-pass translation + guarded literary refinement
- [x] AI QA (structured findings)
- [x] Selective retranslation after a term change (STALE)
- [x] Style presets
- [x] Human review: edit, approve, retranslate one segment, edit history, "save for project" (TM)
- [x] Web UI for all of the above

## Phase 4 — Local ML

- [x] Hardware detection, model registry, recommendation
- [x] Per-task routing presets + per-task models in config
- [x] Confidence-based escalation to a stronger model (`--escalate-model`)
- [x] Single-sample benchmark
- [ ] Measured routing (choosing a model per task from benchmark results automatically)

## Phase 5 — Evaluation

- [x] Local JSONL adapter, chrF++ / term adherence, leaderboard writer
- [x] HF / OPUS / WMT / FLORES adapters (need `datasets` and network; not exercised in CI)
- [x] Dataset license manifest (§46) with a check command
- [x] MTL failure corpus schema
- [x] Experiment matrix A–F on real EPUB pairs (`eval-epub`) — first results in [EVALUATION.md](EVALUATION.md)
- [x] Regression log with tests
- [ ] Larger runs: more chapters, a second book, a larger model, several seeds
- [ ] Human-evaluation data path (rating UI)

## Phase 6 — Comics

- [x] Pages and CBZ import: OCR (RapidOCR, Chinese + English), bubble grouping, reading order, tall-strip tiling (`images.py`)
- [x] Erase lettering (flat fill from the bubble colour, inpaint on art) and typeset the translation in the bubble
- [x] Web: add pages, paste an image from the clipboard, side-by-side page view, fix OCR text, save PNG, download CBZ; CLI: `export --out x.cbz`
- [x] Korean recogniser via external weights (PP-OCRv1-mobile rec + dict, `BABELINK_OCR_MODELS`; E18 measured, upgrade = newer weights)
- [ ] Japanese recogniser (nothing bundled reads it)
- [ ] Vertical text, right-to-left reading order, speaker detection
- [ ] Model-based inpainting for text on textured art; panel detection
- [ ] Score the pipeline on OpenMantra / Manga109 (see [DATASETS.md](DATASETS.md))

## Phase 7 — Live output and production hardening

- [x] Streamed translation text, finished paragraphs visible while a job runs, word-limit batches (`--words`, batch picker), Stop that ends the paragraph being written
- [x] Double-submit protection (client guard, `Idempotency-Key`), duplicate-file refusal, import off the event loop, jobs never overwrite human edits
- [ ] Idempotency and the one-job rule across several server processes or concurrent CLI runs (today: one process)
- [ ] Streaming check against a live OpenAI-compatible endpoint (only a fake stream is tested)
- [ ] Automated test for the page's job-polling recovery after a dropped request (covered by hand only)

## Desktop UI

Replaced for now by the local web UI (ADR-009). A PySide6 shell is not planned until the web UI shows a need for it.
