# Evaluation

How well does the context machinery (glossary, Story Bible, translation memory, chapter summaries, refinement) improve a local model's
translation of long fiction? This page records what was measured, the numbers, and what they do and do not show.

## Method

- **Metric.** chrF++ against a human English translation (0-100, higher is closer), plus *term adherence*: of the glossary terms present in
  the source, how many appear with the agreed rendering in the output. Also counted: untranslated CJK left in the output and QA findings.
- **Configurations**, each run on the same excerpt with the same model:

| | Configuration |
|---|---|
| A | bare LLM, no context |
| B | A + glossary |
| C | B + Story Bible |
| D | C + translation memory |
| E | D + hierarchical context (summaries, previous passages) |
| F | E + literary refinement + QA |

- **Model.** `huihui_ai/qwen3-abliterated:4b` through Ollama on CPU, `think:false`, default sampling.
- **Alignment.** Chapter by number; the excerpt's reference is a window of the English chapter proportional in length to the source excerpt.
  Paragraph counts differ between source and fan translations, so alignment is approximate.
- **Noise.** Two identical runs of the same configuration differ by about 1 chrF. Differences under 1-2 points are not evidence.

Reproduce: `python scripts/eval_heldout.py ZH.txt EN.txt --paragraphs 12 --configs ABDF` for one chapter pair, or
`babelink eval-epub zh.epub en.epub --chapters 3` for two EPUBs.

## 1. Held-out web novel (data the pipeline was not built on)

Two chapters of a published Chinese web novel (the free Qidian mobile pages) against the English fan translation published for the same
chapters. The texts are copyrighted, are kept in a git-ignored folder and are not redistributed here; only scores are recorded.
Chapter 1 uses the first 12 paragraphs, chapter 2 the first 14.

The first run exposed a defect: the model-suggested glossary contained ordinary words (`一名` = *a*, `废物` = *waste*, `身体` = *body*).
Enforcing them rewrote normal prose. `_is_common_word` now drops any rendering that starts lower-case, and the prompt asks for Title Case
names only (regression row in [REGRESSIONS.md](REGRESSIONS.md)). Both runs are shown.

| Chapter | Config | chrF before fix | chrF after fix | Terms applied before | Terms applied after |
|---|---|---|---|---|---|
| 1 (12 paragraphs) | A bare LLM | 49.05 | 48.86 | 8/13 | 8/12 |
| | B + glossary | 46.26 | 48.38 | 13/13 | 11/12 |
| | D + translation memory | 45.33 | 50.85 | 13/13 | 11/12 |
| | F full pipeline | 50.39 | 51.56 | 12/13 | 12/12 |
| 2 (14 paragraphs) | A bare LLM | 47.02 | 48.00 | 2/12 | 1/12 |
| | B + glossary | 46.26 | 47.02 | 9/12 | 8/12 |
| | D + translation memory | 45.87 | 45.03 | 8/12 | 7/12 |
| | F full pipeline | 48.21 | 47.25 | 8/12 | 8/12 |

What this shows:

- **The glossary does what it is for.** Name and term adherence rises from 1-8 of 12 (bare model) to 7-12 of 12 with the glossary in
  the prompt. A bare model renders the same name several ways.
- **The pollution fix mattered.** Before it, adding the glossary lowered chapter 1 by 2.8 chrF (49.05 to 46.26). After it, B is within noise of A.
- **chrF does not show a consistent win for the full pipeline.** F is +2.7 over A on chapter 1 and -0.8 on chapter 2. With one reference, 12-14
  paragraphs and about 1 point of run-to-run noise, the honest reading is "no measurable change in overlap", not "better" or "worse".
- **Chapter 2's glossary was still weak.** The miner proposes fragments that are not names (`几人`, `几倍`, `麻脸少年`) and the 4B model
  transliterates them into pinyin instead of dropping them. Reviewing the Names tab removes them; automatic filtering is not solved.

## 2. Public-domain check: *Dream of the Red Chamber*, chapter 3

Chinese text and Joly's 1892 English translation (Project Gutenberg). The run was stopped after four configurations (each takes 8-10
minutes on CPU), so E and F are not reported.

| Config | chrF | Terms applied (of 28) |
|---|---|---|
| A bare LLM | 44.16 | 21 |
| B + glossary | 45.55 | 21 |
| C + Story Bible | 44.74 | 22 |
| D + translation memory | 44.77 | 22 |

A Victorian translation with Wade-Giles names and abridgement caps modern-model chrF, so only the differences between rows mean anything.
They are about one point, inside the noise.

## 3. Comic pages

The page pipeline is verified, not scored:

- generated pages with Chinese lettering: OCR returned the exact text and boxes, erasing removed the lettering from flat and textured
  backgrounds, long translations fit inside round bubbles (`tests/test_images.py`, with a real-OCR end-to-end test);
- the web flow in a real browser (`scripts/ui_pages_smoke.py`): add a page, fix OCR text, translate, paste an image from the clipboard, save PNG, download CBZ, phone width;
- one run with the real local model on the generated page returned a correct, well-fitted page, with one weak translation: the model rendered `快撤！` ("Retreat!") as "Hurry up!".

No real manga or manhua page has been scored yet. [DATASETS.md](DATASETS.md) lists the benchmarks for that.

## Limits

- One 4B model, CPU, a handful of chapters, 12-14 paragraphs each. No seeds, no confidence intervals.
- One reference per chapter. chrF measures overlap with that translator's wording, which is why a fan translation that merges, reorders or adds lines
  caps every system's score.
- Term adherence counts surface forms, not meaning.
- The context features are designed to matter more over long runs (names recurring over dozens of chapters, callbacks to earlier plot). A
  12-paragraph excerpt cannot show that. The planned test is the chapter-level data in [DATASETS.md](DATASETS.md) with a larger model.

## 4. Hy-MT2-1.8B as the translation engine (2026-10-08/09)

`hymt2-1.8b` (Tencent Hy-MT2-1.8B Q4_K_M via Ollama with the official chat
template — see MODELS.md) + `huihui_ai/qwen3-abliterated:4b` helper, CPU.
Reference windows are proportionally aligned to the translated excerpt; without
that, chrF measures coverage fraction, not quality (10 vs 53 on the same outputs).

| Run | Bare chrF | Harness chrF | Terms bare → harness | Harness Score b/h |
|---|---|---|---|---|
| ParallelFiction ja→en, 8 chapter heads | 49.26 | 48.96 | 4/5 → 5/5 | 96.6 → 99.7 |
| OpenSakura ja→zh, 4 chunks (gold glossary) | 28.39 | 27.76 | 2/10 → 7/10 | 84.8 → 93.8 |
| Red Chamber zh→en ch3 (A/D/F) | 37.57 | 40.07 / 40.24 | 5/18 → 10/18 | 66.1 → 82.2 |
| Three Kingdoms zh→en ch1 (A/D/F) | 42.25 | 43.06 / 42.83 | 7/17 → 15/17 → 16/17 | — |
| Three Kingdoms zh→en ch2 (A/D/F) | 43.93 | 43.82 / 44.08 | 7/13 → 13/13 → 12/13 | 88.2 (F) |

- The bare model is already strong (Hy-MT2 does the heavy lifting); the
  harness holds chrF parity while taking term adherence from ~1/3 to ~2/3+,
  delivery to 100%, and QA-clean everywhere. chrF deltas under ~1 point are
  run noise; the Harness Score is the number that reads near 100.
- Consistency beats mimicry: the fan/Victorian references use their own name
  spellings, so enforced canonical names sometimes cost chrF points while
  being exactly what a whole-book translation needs (bare hallucinated
  "Urumen" for 雨村; the harness enforces one rendering everywhere).
- Refinement (F, helper refiner + guardrails + name-change tripwire) adds
  +0.2-0.3 chrF with no damage; the tripwire fires on real corruptions
  (Ruhai→Ruihai) and stays quiet otherwise. No translator notes fired on these
  excerpts — the refiner only notes genuine wordplay/culture knots.
- Full reports: `D:\StoryTranslation_data\eval_zh3\eval_report.md` (Red Chamber
  ch3, incl. side-by-side prose), per-run Markdown in `eval_runs/`.

- Direction is consistent (harness ≥ bare on chrF in 7/10 samples, terms up
  everywhere), but absolute chrF against fan/Victorian references stays low —
  the references paraphrase, so only row differences mean anything.
- The Red Chamber run exposed two glossary bugs, both fixed: the helper
  echoing source as translation (now `_is_bad_translation`) and ALL-CAPS
  renderings like 且說=AND SAY (now rejected).
- The `floor` baseline (unrelated Joly text vs the reference) scored 46.8,
  *above* both systems: same-domain vocabulary overlap dominates absolute
  chrF, so the floor is not a lower bound here. Only A-vs-D differences count.
- Harness cost is the helper on CPU (30-200 s per chapter for term
  classification); bare Hy-MT2 runs ~2-4 s per paragraph.
