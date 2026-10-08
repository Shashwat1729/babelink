# Known gaps

What the spec describes but this repository does not do, and why. Nothing here is hidden behind a flag that pretends to work.

## Intentionally not built

| Spec area | State | Reason / what is there instead |
|---|---|---|
| Desktop app (PySide6, §74) | not built | A local web UI covers the same workflow (ADR-009). No GUI toolkit dependency. |
| Manga / manhua pipeline (§69, phase 6) | partly built (`images.py`, `[images]` extra) | OCR, bubble grouping, erase and typeset work for Chinese and English lettering on horizontal text. Not built: Japanese/Korean OCR, vertical text, right-to-left order, speaker detection, model inpainting, panel detection. |
| DOCX import/export (§70, §78) | not built | EPUB, TXT, HTML and pasted text cover the target use. |
| Vector / semantic retrieval for TM and context (§21, §73, §80) | not built | Retrieval is exact TM + entity overlap + recency + summaries, inside a size budget. Add an embedding index only if evaluation shows misses. |
| LLM entity extraction and relationship / timeline graph (§11) | not built | Entities are entered by the user or come from mined terms; model-suggested renderings exist for terms, not for relationships. |
| QA thresholds in configuration (§86) | not configurable | Severity rules are fixed in `qa.py`; the config file covers providers, models per task, languages, style and context size only. |
| Human-evaluation rating UI (§59) | not built | Scores are chrF / term adherence only. |
| Licence for this repository (§88) | no licence file yet | The owner must choose one before a public release; until then all rights are reserved. |

## Comic pages: known limits

- OCR models read Chinese and English. Japanese and Korean text is missed or misread, and the OCR is not tuned for hand lettering, sound effects or stylised fonts.
- Lettering on busy art is removed by classic inpainting (OpenCV). Expect smudges there; flat white bubbles come out clean.
- Typesetting uses one system font per target script and fits the text to the bubble. It does not copy the original font, rotation, outline or bubble tails.
- The pipeline is tested on generated pages. It has not been scored on a manga benchmark or on real copyrighted pages (see [DATASETS.md](DATASETS.md) for the plan).
- Term mining needs a name to repeat three times, so a short comic with few pages gets little glossary help. Fix names by hand on the Names tab.

## Limits of the evidence

- The evaluation ([EVALUATION.md](EVALUATION.md)) uses one 4B-parameter local model, a few chapters, the first 12-14 paragraphs of each, and a
  single human translation as the reference (Victorian for the public-domain book, a fan translation for the held-out web novel). chrF against *one* reference measures overlap with that translator's wording, so
  only differences between configurations on the same excerpt mean anything, and small differences are noise.
- HF / OPUS / WMT / FLORES adapters need the `datasets` package and network access and are not exercised by the test suite.
- The web UI is verified with the mock provider in a real browser and with the API tests; long runs with a real model were only exercised through the CLI evaluation.
- The server is built for one local user: no accounts, no TLS, loopback `Host` allow-list. Do not expose it to a network without a reverse proxy that adds authentication.
