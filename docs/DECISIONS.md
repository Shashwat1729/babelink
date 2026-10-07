# Architecture Decision Records

Format: Date / Decision / Context / Consequences.

## ADR-001 — 2026-10-07 — Clean implementation, not a fork

**Context:** Section 89/90 requires auditing existing projects before forking.

**Audit results (2026-10-07):**

| Project | Repo | License | Activity | Notes |
|---|---|---|---|---|
| TranslateBooksWithLLMs | hydropix/TranslateBooksWithLLMs | AGPL-3.0 | Active (Oct 2026) | Full-length book translation, formatting preserved, resumable, multi-provider. Strong reference for pipeline/resumability. AGPL blocks mixing into a permissively-licensed product. |
| Glossarion | Shirochi-stack/Glossarion | AGPL-3.0 | Active (Oct 2026) | Contextual translation, glossary generation, EPUB rebuild. Same AGPL constraint. |
| Glossarion2 | univie-henrikjurek/Glossarion2 | unknown | Stale (Apr 2026) | Self-hosted translation with lexical enhancement; not evaluated further. |
| epublate | madpin/epublate | unknown | Low activity (Apr–Sep 2026) | EPUB translator using LLM; no lore-bible found in audit — needs re-check if considered later. |
| LN-Translator | GPT-LN / community variants | varies | Mixed | Several variants; light-novel focus; no stable canonical repo found in audit. |
| OrionTranslator / ebook_translater / XianScan / translate-book | various | not audited this pass | — | Audit deferred; run before any future fork decision. |

**Decision:** Start a clean implementation (new repo, no copied AGPL code). Use the AGPL projects only as behavioral references (pipeline stages, resumability, glossary UX). Choose our own license later; lean AGPL-3.0 if ecosystem consistency is preferred, MIT/Apache-2.0 if maximal adoption is preferred.

**Consequences:** No license contamination; we must reimplement EPUB IO and provider glue (~small effort with `ebooklib`/`python-epub-ng` and OpenAI-compatible HTTP). No inherited architectural debt.

## ADR-002 — 2026-10-07 — Python as primary language

**Context:** Spec implies LLM/NLP tooling, SQLite, EPUB, local inference (llama.cpp/Ollama/vLLM/Transformers), desktop UI (PySide6 preferred).

**Decision:** Python 3.11+ for core, CLI, providers, QA, evaluation. UI later via PySide6.

**Consequences:** Best ecosystem fit for ML/NLP; CLI-first delivery enables headless use; PySide6 keeps desktop cross-platform.

## ADR-003 — 2026-10-07 — SQLite as system of record

**Context:** Spec §72 mandates SQLite initially.

**Decision:** SQLite for projects, documents, segments, story bible, TM, QA, runs. Vector index in a sidecar (sqlite-vec / FAISS / LanceDB chosen when retrieval quality demands it); must not be required for small projects.

**Consequences:** Zero-config, portable, crash-safe with WAL; sufficient for single-user desktop scale.

## ADR-004 — 2026-10-07 — Story Bible is canonical, never overwritten silently

**Context:** Spec §11, §24, §25.

**Decision:** All AI-derived facts carry status (`inferred|uncertain|suggested|confirmed|user-confirmed`). Contradictions flagged, never silently resolved. Entity merges require explicit status.

**Consequences:** Slightly more UI complexity; essential for long-form consistency.

## ADR-005 — 2026-10-07 — Model routing is per-task, escalation is signal-based

**Context:** Spec §9, §10.

**Decision:** Tasks: analysis/NER, terminology, translation, refinement, QA, difficulty-judgment. Escalation thresholds configurable; deterministic QA failures always escalate.

**Consequences:** Needs confidence heuristics + audit trail of which model ran each segment (model_runs table).

## ADR-006 — 2026-10-07 — Prompt composition over mega-prompts

**Context:** Spec §64–67.

**Decision:** Prompts assembled from versioned components (system, language profile, genre profile, style guide, story bible slice, terminology slice, TM hits, retrieved context, current source). Every run records versions of each component.

**Consequences:** Reproducibility and diffing of prompt changes become first-class.

## ADR-007 — 2026-10-07 — Evaluation-driven development gate

**Context:** Spec §57, §93.

**Decision:** No architectural feature is "done" without a benchmark delta on a fixed dataset slice (spec §57 experiments A–F) on at least one language pair.

**Consequences:** Slower initial velocity, but prevents building generic LLM-wrapper features (spec §101).

## ADR-008 — 2026-10-07 — License posture for datasets

**Context:** Spec §46, §52, §54.

**Decision:** Datasets carry explicit license status fields; redistribution requires `allowed_for_redistribution`. "King of Ten Thousand Years" case study is metadata + user-provided material only, never bundled without verified rights.

**Consequences:** Some evaluation sets must be assembled locally by users; CLI documents this.
