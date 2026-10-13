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

## ADR-009 — 2026-10-07 — Local web UI instead of a desktop shell; one workspace per user

**Context:** Spec §74 prefers a cross-platform desktop UI. A PySide6 app would add a heavy dependency and a second set of code paths for jobs, progress and editing.

**Decision:** Ship a FastAPI app serving a dependency-free HTML/CSS/JS page (`web.py`, `static/`) over the same `service.py` functions the CLI uses. One workspace directory holds `project.db`, `projects/<id>/story_bible/` and `uploads/<id>/`. Long operations run as one background job per project (HTTP 409 if one is already running) that the page polls. Security for a local server: loopback `Host` allow-list (DNS rebinding), `X-Requested-With` header on writes (CSRF), upload extension allow-list, size cap, sanitised names, text-only DOM updates, `no-store`, `nosniff`.

**Consequences:** No GUI toolkit; the UI works on a phone over a tunnel if the user opts in (`BABELINK_HOSTS`). The server is single-user and unauthenticated by design. A desktop shell can wrap the same API later.

## ADR-010 — 2026-10-07 — Configuration file: flag > file > default, unknown keys rejected

**Context:** Spec §86 asks for a readable config for languages, mode, models per task, context size.

**Decision:** Optional `<workspace>/babelink.yaml` or `--config`. Options not given on the command line are filled from the file, then from built-in defaults (`config.py`). Unknown keys, bad providers/styles and bad `models` task names stop the run with a message. Only settings that are wired into behaviour are accepted; QA thresholds and terminology enforcement switches are not (see GAPS.md).

**Consequences:** A typo cannot silently do nothing. Adding a key means wiring it first.

## ADR-011 — 2026-10-07 — Evaluate against real human translations of public-domain books

**Context:** Synthetic samples cannot show whether Story Bible, TM and context help. Spec §51, §57.

**Decision:** `eval-epub` imports a Chinese EPUB and a human English EPUB, aligns chapters by number and the excerpt by proportional length, runs configurations A–F on the same excerpt and model, and scores chrF++ and glossary adherence. Report: `docs/EVALUATION.md`.

**Consequences:** Absolute chrF is low for any modern system against a Victorian translation, and a single reference rewards that translator's wording. Results are used to compare configurations only, and are reported with that caveat.

## ADR-012 — 2026-10-07 — Web UI organised around works; hash routes, no build step

**Context:** The first UI was a project dropdown plus a three-column workbench. The real workflow is: name a novel, add its EPUB, see every chapter, translate, download an EPUB. Choosing a model was a free-text field.

**Decision:** Library of works (`#/`) → work page (`#/work/ID`, tabs Chapters / Names / Story Bible / Quality / Settings) → chapter view (`#/work/ID/chapter/CID`). Adding an EPUB (button, drop zone, several files) imports and analyses it so chapters appear at once. The model picker reads `GET /api/models` (installed models, hardware-fitted suggestions) and `POST /api/models/test`. Theme and accent colour are CSS custom properties on `<html>`, saved in `localStorage`. Plain JS and CSS served as static files; DOM built with `createElement`, never `innerHTML`.

**Consequences:** No bundler or node dependency. The UI is verified by `scripts/ui_smoke.py` in a real browser and by the API tests, not by unit tests of the JS. Finished-job cards belong to the page they ran on and are dropped on navigation.

## ADR-013 — 2026-10-07 — Comic pages reuse the text pipeline; OCR and imaging are an optional extra

**Context:** Manhua and webtoons need the same consistency as novels (names, tone, running jokes), but the input is pixels and the output must be pixels. The old stub (`importers/manga.py`) only raised `NotImplementedError`.

**Decision:** A page becomes one chapter (`documents.format='image'`); each OCR bubble becomes a segment, with its pixel box and OCR line quads in a `regions` table. OCR is RapidOCR (PP-OCR on ONNX Runtime, CPU, Apache-2.0); tall strips are tiled. Lines are grouped into bubbles by proximity (union-find) and ordered top to bottom, then left to right (right-to-left manga order is not handled). Erasing fills the bubble interior with its measured colour, or inpaints (OpenCV TELEA) when the lettering sits on art. The translation is typeset with Pillow, with the font size searched until the text fits the bubble's inscribed rectangle. Everything between OCR and typesetting is the existing pipeline, so glossary, TM, context, refine and QA work unchanged. Dependencies live in the `[images]` extra and `images.py` is imported lazily, so a text-only install is unaffected. Source text can be corrected (`PUT .../segments/{id}/source`), which resets the block to be translated again.

**Consequences:** One code path for quality features. OCR reads Chinese and English only; Japanese and Korean need another recogniser. No vertical text, no model inpainting, no panel detection. Verified on generated pages with real OCR and on the web UI in a browser; not yet scored on a manga benchmark.

## ADR-015 — 2026-10-07 — Stream text into the page; batches by word limit; writes are idempotent and conditional

**Context:** A chapter on a local CPU model takes minutes and the page showed nothing until a paragraph was done. Clicking a button twice (or retrying a slow request) added a chapter twice and started two jobs, and a long job could overwrite text a person had edited meanwhile.

**Decision:** Providers take an optional `on_delta(text_so_far)`; Ollama (NDJSON) and OpenAI-compatible (SSE) stream only when it is set, so the non-streaming path and its tests are unchanged. The pipeline reports each paragraph through `on_text(segment, text, final)`. The job exposes the paragraph being written, its text, the last few finished paragraphs and a word count; the page polls every 600 ms and patches the text into the paragraph card without rebuilding the page. `--words` / `max_words` stops after the paragraph that reaches the limit (never cuts a paragraph; CJK counts one per character). Stop raises `Stopped` from the streaming callback, which closes the stream and cancels generation. Every state-changing request from the page carries an `Idempotency-Key`; the server keeps a bounded in-memory cache and a lock per key, and caches only successes. Job writes use `update_segment(only_if_state=...)` so a person's edit or approval always wins.

**Consequences:** Text is visible within seconds and finished work is readable before the job ends. The idempotency cache lives in the server process, so it does not survive a restart and does not span several server processes; two CLI runs on one project are not guarded against each other. Streaming was checked in a browser against Ollama only; the OpenAI-compatible stream is covered by a unit test with a fake stream, not by a live endpoint. The batch picker applies to the work-wide button, not the per-chapter one.

## ADR-014 — 2026-10-07 — Project renamed from "Story Translate" to Lorebridge (2026-10-13: Lorebridge → Babelink, same rules)

**Context:** "Story Translate" described one of the two inputs (text) and was generic. The project now handles novels, EPUBs and comics, and its core idea is carrying story memory (the lore) across chapters and languages.

**Decision:** Name, package, CLI command, environment variables (`BABELINK_HOME`, `BABELINK_HOSTS`, `BABELINK_FONT`) and config file (`babelink.yaml`) all use `babelink`. The name was free on PyPI and GitHub when checked. Old names are not kept as aliases: there are no external users yet.

**Consequences:** Anyone with a workspace from before the rename keeps working data (`project.db` is unchanged) but must set `BABELINK_HOME` and rename `storytranslate.yaml` if they had one.

