<div align="center">

# Lorebridge

**Long-form fiction translation that keeps every name, voice and plot thread consistent across the whole book.**

Drop in an EPUB, get back a translated EPUB. Built for translators: review names once, proofread side by side, download and go.

Local-first. Runs on your own models. Nothing leaves your machine unless you point it at a remote endpoint.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![tests](https://img.shields.io/badge/tests-127%20passing-2ea44f)](docs/DEVELOPMENT.md)
[![local first](https://img.shields.io/badge/runs-locally%20with%20Ollama-1f2328)](#models)
[![status](https://img.shields.io/badge/status-beta%20(0.2)-green)](docs/ROADMAP.md)

[Quick start](#-quick-start) · [For translators](#-for-translators-proofread--publish) · [Bare model vs harness](#-why-not-just-call-the-model) · [How it works](#-how-it-works) · [Models](#-models) · [Evaluation](#-evaluation) · [CLI](#-command-line) · [Docs](#-documentation)

![Lorebridge web app](docs/screenshots/web-ui.png)

</div>

## Why

Sentence-by-sentence machine translation breaks down on long fiction. The heroine's name comes out three ways, a sect becomes a "gate" in
chapter 4 and a "school" in chapter 40, and the register of a dialogue shifts every few lines. Lorebridge treats a book as one
project with a memory: it remembers how every name was rendered, what happened so far, and what you corrected, and feeds that back into every
later chapter — then proves it with votes, checks and a side-by-side view instead of asking you to trust it.

## ✨ Quick start

```bash
git clone https://github.com/Shashwat1729/lorebridge
cd lorebridge
pip install -e ".[web]"              # add `images` for comics, `dev` for the tests
ollama pull hymt2-1.8b              # translation engine (Tencent Hy-MT2, ~3.5GB)
ollama pull huihui_ai/qwen3-abliterated:4b   # helper: names, summaries, review
lorebridge --project-dir my-books web        # http://127.0.0.1:8000
```

Then: **New work → drop your EPUB → Translate → Download translated EPUB.** The app picks the right engine per language automatically
(CAT-Translate for Japanese→English, Hy-MT2 for the rest — see [Models](#-models)).

Requires Python 3.11+ and [Ollama](https://ollama.com) for local translation. Without a model the app runs in demo mode
(the mock provider copies the text), which is useful for trying the interface.

## 📖 For translators: proofread & publish

Translating a chapter is three clicks. Proofreading it is the actual job, so the UI is built around that:

1. **Translate one chapter or the whole book.** Each chapter row has its own **Translate** button with live progress;
   **Translate remaining** runs the book. The translation streams in paragraph by paragraph — Stop ends the current one.
2. **Review names once.** The Names tab lists every mined name with its proposed rendering — confirm, edit or ignore.
   Your decisions lock in and propagate to every later chapter (paragraphs that used an old rendering are marked outdated).
3. **Proofread side by side.** Each chapter opens as original | translation paragraph pairs with edit, approve,
   retranslate-per-paragraph and a look at exactly what context the model was given (**Context**).
   The **Proofread** button switches to a continuous two-column reading view (original left, translation right,
   quality warnings edge-marked) that prints cleanly for paper review.
4. **Download and go.** **Download translated EPUB** writes the translation back into your original EPUB —
   layout, images and table of contents kept — and warns before exporting an unfinished book.
   Tick *Show the original under each paragraph* for a bilingual edition, or take plain text (.txt).
   Approved paragraphs are never overwritten by later runs.

| Chapter view | Proofread reading view | Phone |
|---|---|---|
| ![](docs/screenshots/web-ui-chapter.png) | ![](docs/screenshots/web-ui-page.png) | ![](docs/screenshots/web-ui-phone.png) |

| Names review | Model picker | Context inspector |
|---|---|---|
| ![](docs/screenshots/web-ui-dark.png) | ![](docs/screenshots/web-ui-model.png) | ![](docs/screenshots/web-ui-context.png) |

> Screenshots follow the current UI; the proofread view is the newest addition.

## 📚 What it translates

| Format | Example tested | Engine | Status |
|---|---|---|---|
| Chinese web novel (zh→en) | Red Chamber ch.3 vs Hawkes | Hy-MT2-1.8B | 13/13 terms, Harness 97 |
| Japanese light novel (ja→en) | ParallelFiction bake-off | CAT-Translate-1.4B | beats Hy-MT2 head-to-head |
| Japanese light novel (ja→zh) | OpenSakura, gold glossary | Hy-MT2-1.8B | 9/9 terms vs 1/9 bare |
| Japanese classic (ja→en) | Rashōmon, 32 paras | CAT-Translate-1.4B | 32/32 complete, QA 5→1 |
| Chinese manhua page (zh→en) | Martial Peak scanlation page | OCR + Hy-MT2 | 7/7 blocks, 33s, typeset |
| Korean manhwa (ko→en) | Nano Machine | OCR + Hy-MT2 | engine proven (ko→en smoke ✓); page loop waits on story-page raws — aggregators serve covers statically, chapters render in JS |
| Japanese manga (ja→en) | One Piece class | — | blocked on Japanese OCR (no usable open weights; same per-script slot as Korean, ready to fill) |

Direction-aware default (`--provider auto`) picks the engine; every other direction falls back to Hy-MT2,
which also handles ko→en and ja→en (smoke-tested). EPUB in, translated EPUB out for prose; pages in, CBZ out for comics.

## 🥊 Why not just call the model?

A bare translation model on its own is fluent and inconsistent. Measured head-to-head on real books
(Hy-MT2-1.8B, same passages, same model — the only difference is the harness):

| | Bare model call | Lorebridge harness |
|---|---|---|
| Name/term adherence, OpenSakura ja→zh (9 gold terms) | 1/9 | **9/9** |
| Name/term adherence, Red Chamber ch.3 (13 glossary terms) | 2/4 (early) | **13/13** |
| Harness Score (terms applied · complete delivery · QA-clean · nothing refused) | 80 | **97** |
| QA warnings, Rashomon 32-paragraph run | 5 | **1** (both 32/32 complete, zero untranslated text left) |
| Fluency (chrF vs human reference) | — | within ±2 (see note) |

Note: chrF against published human translations barely moves — those references are free literary rewrites
(Victorian-era Hawkes, fan prose), and no orchestration makes a 1.8B model write like them. The harness wins
everything a novel actually needs: the same name rendered the same way in chapter 1 and chapter 80,
nothing skipped, nothing refused, everything checked. Fluency comes from the engine (see [Models](#-models));
fidelity comes from the harness.

**What the harness does that a bare call cannot:**

- 🔒 **Names that stay put** — mined once, confirmed by you, enforced in every later chapter; the book itself
  votes on the final surface and losing variants are rewritten deterministically.
- 🗳️ **Elections, not guesses** — when sources disagree on a rendering, shipped outputs elect the winner by majority.
- 🗣️ **Phonetic authorities** — vendored Unihan reading tables reject misread names (黛玉="Da Yu", 松本="Matsubara")
  before they can ever enforce, in Chinese and Japanese.
- 🧩 **Piecewise long paragraphs** — long paragraphs translate as sentence pieces sharing one glossary context
  (+1.2 chrF on the benchmark, and faster).
- 🛡️ **Delivery guarantees** — retries, refusal handling, provider failover chains, deterministic QA
  (missing text, prompt leaks, leftover source language, number mismatches, terms not applied), contradiction
  detection across chapters.
- 📚 **Project memory** — chapter summaries, story bible, translation memory: earlier lines and events feed every
  later paragraph, and your edits are remembered for similar lines.

## ⚙️ How it works

```
EPUB / TXT / pasted text / comic pages (OCR)
   -> chapters -> segments (paragraphs, or speech bubbles)
   -> term mining (burstiness statistics, no word lists)
   -> helper classifies + translates names -> translator-agreement gate -> you confirm
   -> context per segment: agreed names, summaries, previous passages, translation memory
   -> translation (piecewise for long paragraphs, placeholders for names on narrow engines)
   -> book election + deterministic normalization -> QA (+ optional AI review)
   -> your edits and approvals
   -> translated EPUB / TXT (bilingual optional), or re-lettered pages (PNG / CBZ)
```

Everything lives in one SQLite file plus editable JSON Story Bible files (`project.db`, `projects/<id>/story_bible/*.json`).
Segments move through `UNPROCESSED → ANALYZED → TRANSLATED → REFINED → QA_PASSED / NEEDS_REVIEW / HUMAN_APPROVED`,
and `STALE` when a name they use changes. Design notes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/DECISIONS.md](docs/DECISIONS.md). Every experiment and its result: [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md).

## 🤖 Models

Small, specialized, local. The app picks the engine per translation direction automatically (`--provider auto`, the default):

| Direction | Engine | Size | Why |
|---|---|---|---|
| ja → en | CAT-Translate-1.4B (Ollama `cat14b`) | ~1.9GB | Beats Hy-MT2 head-to-head on fiction ja→en (bake-off: bare chrF 46.8–58.2) |
| zh → en, others | Hy-MT2-1.8B (Ollama `hymt2-1.8b`) | ~3.5GB | Translation-only model from Tencent; needs its official chat template (bundled) |
| Helper (all) | `huihui_ai/qwen3-abliterated:4b` | ~2.5GB | General LLM for name classification, summaries, review — never for translation itself |

```bash
ollama pull hymt2-1.8b
ollama pull huihui_ai/qwen3-abliterated:4b
# Japanese novels: build the CAT model from its GGUF (see docs/MODELS.md)
```

Any model Ollama serves, or any OpenAI-compatible endpoint, also works — a larger model is the single biggest
fluency lever. The registry and the picker ([docs/MODELS.md](docs/MODELS.md)) recommend models for your hardware.
Qwen3-family models are called with thinking disabled. The helper runs at a reduced context (4k) so it fits
alongside the engine on a 16GB CPU-only machine.

## 📊 Evaluation

Measured against human translations with chrF and term adherence, plus the Harness Score (terms applied,
complete delivery, QA-clean, nothing refused) that tracks what the harness controls:

- **OpenSakura** ja→zh, gold glossary: **9/9 terms vs 1/9 bare** — terminology that actually counts.
- **Red Chamber** zh→en ch.3 vs Hawkes: **13/13 terms, Harness 97**, chrF 41.8 → 43.0 with piecewise.
- **ParallelFiction** ja→en bake-off: CAT-Translate wins the engine choice; harness matches bare fluency ±2.
- **Rashomon** ja→en long-form (32 paragraphs): 32/32 complete both legs, QA warnings 5 → 1.
- **Full book**: 1,648 chapters / 81,798 segments audited with 0 tag/segment issues; exported EPUB validates.
- 127 offline tests, every bug a regression test plus a row in [docs/REGRESSIONS.md](docs/REGRESSIONS.md).

Numbers, method and honest limits: [docs/EVALUATION.md](docs/EVALUATION.md). Corpora and what's next:
[docs/DATASETS.md](docs/DATASETS.md). What is not built: [docs/GAPS.md](docs/GAPS.md).

## 💻 Command line

```bash
lorebridge --project-dir demo import novel.epub --source-lang zh
lorebridge --project-dir demo analyze 1 --provider auto        # mine names, propose renderings
lorebridge --project-dir demo terminology 1 --accept-all
lorebridge --project-dir demo translate 1 --provider auto
lorebridge --project-dir demo qa 1
lorebridge --project-dir demo export 1 --out translated.epub
lorebridge --project-dir demo validate 1 --file translated.epub
```

Continue a book chapter by chapter:

```bash
lorebridge --project-dir demo paste 1 --file chapter12.txt --title "Chapter 12" --translate --provider auto
cat chapter13.txt | lorebridge --project-dir demo paste 1 --translate --provider auto
```

One chapter out as text: `lorebridge --project-dir demo export 1 --out chapter.txt` (add `--bilingual` for original + translation; `.epub`/`.cbz` pick their writer from the extension).
Score a run against a human translation: `lorebridge eval-epub original.epub human.epub --chapters 3 --configs ADF`.

Other commands: `projects`, `chapters`, `terminology`, `edit`, `approve`, `retranslate --term X --new Y`, `review`,
`storybible`, `style`, `models`, `refine`, `benchmark`, `eval-epub`, `datasets`.

### Configuration

`<project-dir>/lorebridge.yaml` or `--config FILE`; command-line flags win over the file, and unknown keys are rejected.

```yaml
provider: auto
model: ""            # empty = direction-aware default engine (CAT for ja->en, Hy-MT2 otherwise)
target_lang: en
style: literary                    # literal | faithful-natural | literary | highly-literary
n_prev: 4                           # earlier translated passages shown to the model
```

## 📚 Documentation

| | |
|---|---|
| [EXPERIMENTS](docs/EXPERIMENTS.md) | Every hypothesis, result and failed approach (E01–E17) |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | Components, data flow, the comic-page pipeline |
| [DECISIONS](docs/DECISIONS.md) | Design decisions and why |
| [EVALUATION](docs/EVALUATION.md) | Results, method, caveats |
| [DATASETS](docs/DATASETS.md) | Corpora used and the next ones to add, with licences |
| [GAPS](docs/GAPS.md) | What is not built and where the evidence is thin |
| [ROADMAP](docs/ROADMAP.md) | What is done and what is next |
| [MODELS](docs/MODELS.md) | Model registry and recommendations |
| [REGRESSIONS](docs/REGRESSIONS.md) | Every bug found and the test that guards it |
| [DEVELOPMENT](docs/DEVELOPMENT.md) | Setup, layout, checks |

## Manhua and comics

Real pages, measured — not a mockup. A genuine Martial Peak scanlation page (MangaDex, English) through the full loop:

| Step | Measured |
|---|---|
| OCR (English lettering, incl. embedded Chinese credit lines) | 7/7 text blocks found in 4s |
| Translation en→zh (Hy-MT2-1.8B) | 7/7 blocks, 33s total |
| Typeset back onto the page / CBZ export | rendered, lettering erased + refit per bubble |

Korean pages use a Korean recognition model (official PP-OCR weights, fetched to your machine, no new packages):
the default engine reads near-nothing on Korean text while the Korean engine finds every content line —
with honest limits (the 2021-era mobile model misreads ~1 syllable in 4; a newer weight file drops in with no code change).
Japanese pages still need another recognizer.

What this proves and what it doesn't: OCR→translation→typesetting works on real art (credit-page wording translates
cleanly; the series title comes out 武境峰 — an unknown name transliterated, which is exactly what the Names review
step is for). Page-level proof on Korean raws (e.g. Nano Machine) waits on raw acquisition, not on code — the fetch
attempts and protocol are logged in [docs/DATASETS.md](docs/DATASETS.md), and evaluation images are never committed
to the repo (copyright stays with its owners; the numbers above are reproducible with your own raws).

1. **Read.** RapidOCR finds each line of text and its position (Chinese, English, Korean rec models). Tall webtoon strips are cut into overlapping tiles first. Lines are grouped into
   speech bubbles and put in reading order.
2. **Translate.** Each bubble is a segment in the normal pipeline, so names, memory and context work exactly as they do for a novel.
3. **Replace.** The old lettering is erased (filled with the bubble's own colour, or inpainted when it sits on artwork) and the translation is
   set in the same bubble, with the font size fitted to the space. Compare original and translated side by side on the page view.
4. **Correct and export.** Fix what the OCR misread (including Korean slips), save a page as PNG or download everything as a CBZ.

```bash
pip install -e ".[web,images]"
lorebridge --project-dir demo import page.png --name "Demo" --source-lang zh --target-lang en
lorebridge --project-dir demo translate 1 --provider auto
lorebridge --project-dir demo export 1 --out translated.cbz
```

**Limits to know about.** The bundled OCR reads Chinese and English; Japanese and Korean need another recogniser. Vertical text, right-to-left
reading order, sound effects and model-based inpainting are not built. Text on busy art leaves smudges. It has been tested on generated pages and in a
browser, and has not been scored on a manga benchmark. Details in [docs/GAPS.md](docs/GAPS.md).

The server only accepts loopback `Host` headers (add others with
`LOREBRIDGE_HOSTS`) and requires an `X-Requested-With` header on every write. It has no accounts or TLS, so do not expose it to a network.
Theme (system, light, dark) and accent colour are under the circle icon.

## Development

```bash
pip install -e ".[web,images,dev]"
python -m pytest -q                                      # 123 tests; no network or model needed
python scripts/ui_smoke.py --url http://127.0.0.1:8765   # novel flow in a real browser (needs playwright)
python scripts/ui_pages_smoke.py --url http://127.0.0.1:8765   # comic-page flow in a real browser
python scripts/ui_live_smoke.py --url http://127.0.0.1:8765    # live streaming, Stop, double click (needs a running Ollama model)
```

Source layout:

```
src/lorebridge/   pipeline, context, terminology, story bible, qa, refinement, importers, exporters, providers,
                  images (comic pages), web + static UI, evaluation
tests/            pytest suite
scripts/          corpus download, held-out evaluation, browser smoke tests, sample page generator
docs/             architecture, decisions, evaluation, datasets, gaps, roadmap
```

## License

No licence has been chosen yet, so all rights are reserved by default. Add a `LICENSE` file before sharing the code.

Evaluation texts are public-domain Project Gutenberg files fetched on demand, or copyrighted chapters kept locally and never committed. Comic-page OCR uses
[RapidOCR](https://github.com/RapidAI/RapidOCR) (Apache-2.0). Reference projects were studied; no code was copied from them.
