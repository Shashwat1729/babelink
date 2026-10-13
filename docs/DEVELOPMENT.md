# Development

## Setup

```bash
pip install -e ".[web,images,dev]"     # images = comic-page OCR (rapidocr-onnxruntime, opencv, numpy, Pillow)
python -m pytest -q          # 128 tests, no network, no model; image tests skip without the extra
```

## Layout

- `src/babelink/` — package. `service.py` holds the operations shared by CLI and web (create project, import, paste, overview); `pipeline.py` analyse/translate; `context.py` prompt context; `terminology.py`, `story_bible.py`, `qa.py`, `refinement.py`, `review.py` (human edits); `exporters/`; `web.py` + `static/` (UI); `config.py` (YAML); `eval*.py`, `datasets.py`, `dataset_manifest.py` (evaluation).
- `tests/` — `test_pipeline.py` (importers, segmentation, E2E EPUB/TXT), `test_phase2.py` (Bible/context/summaries/contradictions), `test_phase3.py` (refinement/retranslate), `test_phase4*.py` (registry/routing/escalation), `test_phase5.py` (datasets/eval), `test_continuity.py` (chapter-wise paste reuses project memory), `test_quality.py` (QA, review, export, validation, metrics, providers), `test_web.py` (API, security guards, jobs), `test_cli.py` (CLI flow, config), `test_live.py` (streaming, word-limit batches, double-submit and race guards, Stop), `test_formatting.py`, `test_images.py` (comic pages: grouping, erase, typeset, CBZ, web, real OCR).
- `scripts/fetch_corpus.py` — download the public-domain evaluation EPUBs to `corpus/` (git-ignored).
- `scripts/ui_pages_smoke.py` — same idea for comic pages: add a generated page (real OCR), fix text, translate, paste an image from the clipboard, save PNG, CBZ, phone width. `scripts/make_sample_page.py` draws the page.
- `scripts/eval_heldout.py` — A–F matrix on one chapter + a published translation kept as plain text (see EVALUATION.md).
- `scripts/ui_live_smoke.py` — real browser against a real Ollama model: double click on "Add and translate" sends one request, text appears while it is written, a finished paragraph is visible before the job ends, Stop works mid-paragraph, the batch picker sends a word limit, phone width. Takes a few minutes on CPU.
- `scripts/ui_smoke.py` — drives the web UI in a real Chromium with the mock provider (new work from a generated EPUB, translate, names, edit, paste a follow-up chapter, checks, EPUB download, theme/accent, model picker, phone width, delete) and writes screenshots.

## Rules

- Check `docs/MASTER_PROJECT_SPEC.md` before architectural changes.
- Every bug becomes a regression test (spec §92) and a row in `docs/REGRESSIONS.md`.
- Every significant feature is evaluated against a dataset slice (spec §93, ADR-007).
- No cloud-mandatory path; no silent Bible overwrites; no automatic propagation of edits; `HUMAN_APPROVED` text is never overwritten by a run.
- The frontend never uses `innerHTML`; build DOM with `el()` / `textContent`. Uploaded names are sanitised; writes need the `X-Requested-With` header.

## Checks

```bash
python -m pytest -q

# UI in a real browser (pip install playwright; CHROMIUM=<chrome.exe> reuses an installed browser)
BABELINK_HOME=eval_runs/ui_ws python -m uvicorn babelink.web:app --port 8765 &
python scripts/ui_smoke.py --url http://127.0.0.1:8765 --out eval_runs/ui

# CLI smoke with the mock provider
babelink --project-dir demo import sample.epub
babelink --project-dir demo analyze 1
babelink --project-dir demo translate 1 --provider mock
babelink --project-dir demo export 1 --out out.epub && babelink --project-dir demo validate 1 --file out.epub

# real-corpus evaluation (needs Ollama and a model; hours on CPU)
python scripts/fetch_corpus.py
babelink --project-dir eval_runs/real eval-epub corpus/zh_24264.epub corpus/en_9603.epub --provider ollama --model huihui_ai/qwen3-abliterated:4b --chapters 3,4
```

The mock provider echoes the source, so validation of mock output reports "untranslated" on purpose.
