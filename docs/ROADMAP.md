# Roadmap

Phases follow spec §91. Each phase ends with an evaluation gate (ADR-007): benchmark delta vs
previous phase on fixed dataset slices.

## Phase 1 — Core (MVP)

- [ ] Project scaffolding (Python package, CLI `storytranslate`)
- [ ] SQLite schema + migrations for projects/documents/chapters/scenes/segments/translations/model_runs
- [ ] EPUB importer/exporter (chapters, structure preserved)
- [ ] TXT importer
- [ ] Language detection
- [ ] Chapter/scene segmentation
- [ ] OpenAI-compatible provider + Ollama provider (+llama.cpp later)
- [ ] Glossary/terminology store (basic)
- [ ] Translation memory store
- [ ] Resumable chapter-level checkpointing
- [ ] Deterministic QA (paragraph/number/name checks)
- [ ] CLI: import / analyze / translate / qa / export

**Gate:** end-to-end EPUB→EPUB on one local model; resume after kill; terminology consistency on a cultivation sample.

## Phase 2 — Story Intelligence

- [x] Story Bible (JSON sections; user-editable)
- [x] Terminology extraction into Bible + recurring-term mining
- [x] Chapter/arc summaries
- [x] Hybrid context retrieval (entity overlap + keyword + recency)
- [x] Context budget assembly
- [x] Contradiction detection (terminology drift)
- [ ] Entity extraction + resolution statuses (LLM-backed; Phase 2.5)
- [ ] Progressive terminology confirm/edit/ignore UI flow (CLI now, UI later)
- [ ] Full relationships/timeline tracking

## Phase 3 — Quality

- [x] Two-pass translation + literary refinement with guardrails
- [x] AI QA structured findings (provider.review, mock-safe)
- [x] Selective retranslation via STALE + term change
- [x] Style preset stored in Story Bible; injected into refinement prompt
- [ ] Human review states UI + edit history (CLI only)
- [ ] Selective retranslation count surfaced in CLI output

## Phase 4 — Local ML

- [x] Hardware detection (RAM/VRAM/CPU)
- [x] Model registry (starter list) + per-pair recommendation
- [x] Benchmark harness (single-sample latency)
- [ ] Per-task model routing presets
- [ ] Confidence-based escalation
- [ ] Full benchmarking harness (tok/s, memory, cancellable)

## Phase 5 — Evaluation

- [ ] Dataset adapters (HF, OPUS, WMT, FLORES, local)
- [ ] MTL failure corpus schema
- [ ] Benchmark runner (experiments A–F)
- [ ] Leaderboard + human-eval UI data path
- [ ] Regression suite from real bugs

## Phase 6 — Comics

- [ ] Manga/manhua adapter: OCR → regions → panels → context → translation → inpainting → typesetting

## Desktop UI

PySide6 app over the same core, after Phase 3 stabilizes the core APIs.
