# Development

## Setup

```bash
pip install -e .
python -m pytest -q
```

## Layout

- `src/storytranslate/` — package
- `tests/` — pytest; `test_pipeline.py` (E2E EPUB/TXT), `test_phase2.py` (Bible/context/summaries/contradictions), `test_phase3.py` (refinement/QA/retranslate), `test_phase4.py` (registry/benchmark), `test_phase5.py` (dataset/eval)

## Rules

- Check `docs/MASTER_PROJECT_SPEC.md` before architectural changes.
- Every bug becomes a regression test (spec §92).
- Every significant feature is evaluated against a dataset slice (spec §93, ADR-007).
- No cloud-mandatory path; no silent Bible overwrites; no automatic propagation of edits.

## CI smoke

```bash
python -m pytest -q
storytranslate --project-dir demo import sample.epub
storytranslate --project-dir demo analyze 1
storytranslate --project-dir demo translate 1 --provider mock
```
