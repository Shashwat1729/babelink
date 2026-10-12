# Research: models, datasets, methods (2026-10-10)

Survey before building. Sources: HF model cards, Hy-MT2 paper (arxiv 2605.22064),
CAT-Translate paper (arxiv 2606.21413), WMT25 terminology findings, NAACL 2025
LITEVAL, EMNLP 2025 LITRANSPROQA, OpenSakura/SakuraLLM repos, GuoFeng repo,
Baka-Tsuki about page, jp-tl-bench.

## 1. Translation engines

| Candidate | Pair | Size / quant | License | Verdict |
|---|---|---|---|---|
| Hy-MT2-1.8B Q4_K_M (current) | 33 langs incl zh/ja↔en | 1.1GB | Apache-2.0 | **KEEP as default.** WMT25-winning lineage; 1.8B beats commercial APIs per paper; official terminology block matches our glossary design |
| Hy-MT2-1.8B 1.25-bit GGUF | same | **440MB** | Apache-2.0 | This is the user's "<0.5GB" model. TEST as speed option (1.5x faster per Tencent); quality TBD on fiction |
| Hy-MT2-7B GGUF | same | ~4.5GB | Apache-2.0 | Quality option for bigger boxes only (16GB CPU box must load sequentially). Not default |
| Hy-MT2-30B-A3B | same | ~17GB+ | Apache-2.0 | Out of scope local; API-only reference |
| CAT-Translate-1.4B (CyberAgent, MIT) | ja↔en specialist | 931MB Q4 GGUF via mradermacher | MIT | **BAKE OFF vs Hy-MT2 on ParallelFiction.** Beats TranslateGemma-12B on business/medical/patent — fiction untested. If it wins ja→en, it becomes the ja engine |
| CAT-Translate-3.3B | ja↔en | ~2GB | MIT | Bake-off runner-up if 1.4B disappoints |
| SakuraLLM 7B/13B/14B | ja→zh only | 5–12GB | CC BY-NC-SA | **REJECTED.** Wrong target pair, too big, non-commercial license, Kaggle-banned upstream |
| Qwen3-4B-class general LLMs | any | 2.5GB+ | Apache-2.0 | Helper role only (see below), never the engine: our own A/B shows MT engine + glossary beats general-LLM prose |
| Tower / EuroLLM / Translategemma | en-centric pairs | 4–12B | various | No zh/ja fiction advantage over Hy-MT2; skip |

Helper (terms/refine/review/summaries — needs a general LLM since Hy-MT2 is
translation-only): **keep huihui_ai/qwen3-abliterated:4b at 4k ctx.** Abliterated
matters: fiction (classical + adult content) trips refusal-tuned models; the 4k
ctx cap is a measured OOM constraint on this box (memory #27), not a guess.

## 2. Datasets

| Dataset | Pair | What it is | Verdict |
|---|---|---|---|
| Red Chamber EPUBs (local corpus/) | zh→en | 108-chapter classic + Victorian human translation, chapter-aligned | **Current workhorse, keep.** Classical zh is the hardest mining/term case |
| GuoFeng Webnovel V1 (Tencent/China Literature) | zh→en | 22,567 chapters, 179 web novels, DOCUMENT-level sentence-aligned, professional translators, CC-BY 4.0 | **Gold standard — BLOCKED on user action.** Registration wall (Google/Tencent form + Dropbox). USER: register, download, place under `D:\StoryTranslation_data\datasets\guofeng\` |
| ParallelFiction-Ja_En-100k | ja→en | 106k chapters, sentence-aligned, fan translations (quality varies) | **Keep for ja.** Compare configs, never absolutes |
| OpenSakura-DS-260220-LN-ja-zh | ja→zh | 631k rows WITH gold glossary per row | **Keep + new eval mode** (see §4): gold glossary isolates application from mining |
| Baka-Tsuki | ja→en refs only | 160 EN fan projects, wiki, no aligned JP sources, licensed titles removed | **REJECTED as parallel corpus.** English-only hosting; nothing to align against |
| FLORES-200 / WMT news sets | various | General-domain | Rejected for fiction tuning (domain mismatch); Hy-MT2 paper numbers suffice as sanity anchor |

## 3. Methods (what the literature says)

- **Glossary-in-prompt + iterative repair is SOTA practice**, not a hack: WMT25
  terminology systems converge on (a) term extraction, (b) glossary-in-prompt,
  (c) LLM post-edit enforcing constraints, (d) retry-until-compliant. Our
  mine→classify→enforce→guided-retry→QA chain matches this shape. Keep it;
  improve its pieces, don't replace the shape.
- **Measure consistency, not just adherence** (Semenov & Bojar, WMT25): a term
  rendered 3 ways across chapters fails even if each matches some glossary.
  Implement pseudo-reference consistency (majority rendering per source term
  across the book) as a deterministic eval metric. No model cost.
- **String metrics are weak for literature** (NAACL 2025: auto metrics ≤20%
  adequate; chrF can't see Wade-Giles↔pinyin equivalence). chrF stays a
  secondary signal; primary = term adherence + consistency + Harness score.
- **LLM-judge deferred**: LITRANSPROQA/Prometheus need 7B+ judges; nothing on
  this box qualifies. Optional later experiment (helper-as-judge pairwise),
  not a gate.
- **Document-level context**: KIKIS-style (prev-context + chapter background)
  matches our n_prev + summaries + bible. No change.
- **No training.** Frozen weights + harness engineering. Kaggle/GPU rejected:
  same weights run slower locally, and evals must reflect the product box.

## 4. Actions (in order)

1. CAT-Translate-1.4B vs Hy-MT2 bake-off on ParallelFiction slice (D: only).
   Winner takes ja→en default; loser stays fallback-chain member.
2. Term-consistency metric in eval (deterministic, pseudo-reference).
3. Gold-glossary eval mode on OpenSakura rows (supply gold terms, measure
   application) — unconfounds mining from enforcement.
4. Hy-MT2 1.25-bit 440MB smoke test (speed/quality vs Q4_K_M).
5. GuoFeng V1 full eval once the user completes the download (registration wall).
