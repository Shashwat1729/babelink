# Datasets

## Evaluation format (spec §49)

One JSON object per line:

```json
{"id":"1","source_language":"zh","target_language":"en","source":"...","reference":"...","context":"...","genre":"cultivation","domain":"web_novel"}
```

## Running a benchmark

```bash
lorebridge benchmark-run data/eval.jsonl --provider ollama --model huihui_ai/qwen3-abliterated --out leaderboard.md
```

## Legal posture (spec §46, §54)

Every dataset must record license fields before use. Redistribution requires
`allowed_for_redistribution`. Do not bundle copyrighted references. The
"King of Ten Thousand Years" case study is metadata + user-provided material only.

## Real-corpus evaluation set (public domain, spec §46, §51)

`python scripts/fetch_corpus.py` downloads Project Gutenberg EPUBs into `corpus/` (git-ignored, never redistributed by this repo).
`lorebridge datasets` prints the manifest (`src/lorebridge/dataset_manifest.py`): id, licence, redistribution flag, source URL.
`lorebridge datasets --check` exits 1 if an entry lacks a licence field.

| id | Language | Work | Use |
|---|---|---|---|
| gutenberg-24264 | zh | 紅樓夢 (Dream of the Red Chamber), 120 chapters | source text for `eval-epub` |
| gutenberg-9603 | en | Joly's translation, Book I (chapters 1-24 per Gutenberg) | human reference for chapters that exist in both |
| gutenberg-51828 | zh | 聊齋志異 (short stories) | importer / chapter-split robustness; no paired translation |
| gutenberg-9604 | en | Joly, Book II (chapters 25-56) | reference for later chapters |

Public-domain status depends on jurisdiction; check yours before redistributing derived files.
Alignment is by chapter number plus a proportional-length window, not by paragraph (see `eval_real.py`).

## Network adapters

`HuggingFaceAdapter`, `OPUSAdapter`, `WMTAdapter`, `FLORESAdapter` in `datasets.py` load a split through the `datasets` package
(`pip install datasets`) and normalise it to the format above. They need network access and are not run in CI.

## Next datasets (researched, none downloaded or wired in yet)

All of these are in `lorebridge datasets` with their licence fields; none is redistributed by this repository. Licence statements come from
the linked paper or dataset page when the research was done (2026-10-07). Check the current terms before downloading.

### Chinese web novels and long-form fiction (text pipeline)

| Dataset | What it is | Why it matters here | Use |
|---|---|---|---|
| [WMT23 literary task](https://arxiv.org/abs/2311.03127) | Document-level zh-en web-novel corpus, CC BY-NC-ND 4.0 per the task paper | Same genre as the target books; document level | Replace the 12-paragraph excerpts with whole chapters; the best next step |
| [GuoFeng Webnovel](https://github.com/longyuewangdcu/GuoFeng-Webnovel) | Tencent AI Lab / China Literature web fiction, 14 genres, multilingual | Source of the WMT23 data; more genres and books | Cross-genre check; gated by its research terms |
| [BWB](https://arxiv.org/abs/2210.14667) | Chinese novels with expert English translations; test set annotated for pronouns, entities and discourse | Measures the failures this project targets (who "he" is, same entity same name) | Consistency score alongside chrF |
| [Ch2Ch](https://arxiv.org/abs/2407.08978) | 160 zh-en books for chapter-to-chapter translation | No sentence alignment needed, the same shape as chapter-wise paste | Long-range context test with many chapters of one book |
| [Par3](https://arxiv.org/abs/2210.14250) | Public-domain novels, paragraph-aligned to human and machine English | Redistributable references; human preference data | Paragraph-level checks that can live in CI-like runs |
| [DocHPLT](https://arxiv.org/abs/2508.13079) | 124M document pairs, 50 languages with English | Web text, not fiction | Only if fine-tuning is ever tried |

Metrics to add with them: BlonDe ([arXiv 2103.11878](https://arxiv.org/abs/2103.11878)) for document-level discourse, and name-consistency counts
across a whole book (the same source name should map to one rendering).

### Comics (image pipeline)

| Dataset | What it is | Use |
|---|---|---|
| [OpenMantra](https://github.com/mantra-inc/open-mantra-dataset) | 214 manga pages, 1,593 sentences, five genres, annotated text and references (University of Tokyo, with [the manga translation paper](https://arxiv.org/abs/2012.14271)) | End-to-end page score; needs a Japanese recogniser first |
| [Manga109](http://www.manga109.org/en/) | 109 comic books, 21,142 pages; academic non-profit use by author permission (some works also allow industrial use) | Text-box ground truth for OCR and detection recall |
| [MangaSeg](https://huggingface.co/datasets/MS92/MangaSegmentation) (CVPR 2025) | Pixel masks for Manga109 | Scores how clean text erasing is, which bounding boxes cannot |
| [Manga text detection](https://arxiv.org/abs/2009.04042) | Pixel-level text annotations | A second mask set for erase quality |

Chinese manhua and webtoon sets of this kind are scarce. Until one is found, the generated pages in `scripts/make_sample_page.py` are the only test.

### Existing tools to learn from (not dependencies)

- [RapidOCR](https://github.com/RapidAI/RapidOCR) (Apache-2.0): the OCR this project uses (PP-OCR models on ONNX Runtime). Chinese and English only.
- [manga-image-translator](https://github.com/zyddnys/manga-image-translator) (GPL-3.0), [comic-translate](https://github.com/ogkalu2/comic-translate) (Apache-2.0) and
  [comic-text-detector](https://github.com/dmMaze/comic-text-detector) (GPL-3.0): full comic pipelines with bubble detection and model inpainting. They show what is
  missing here (see [GAPS.md](GAPS.md)). No code was copied; code under GPL-3.0 would bind this repository's licence.

### Suggested order

1. WMT23 literary + BWB: whole-chapter runs with a larger model, scoring chrF, BlonDe and name consistency.
2. Par3 for a small redistributable regression set.
3. A Japanese recogniser, then Manga109 / MangaSeg for detection and erase quality, then OpenMantra end to end.

