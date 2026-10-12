# Experiment ledger: long-form literary translation research

Permanent record. One entry per loop: baseline → hypothesis → intervention →
measurement → verdict (keep/kill). Numbers are chrF / term adherence /
Harness Score unless noted. Models: Hy-MT2-1.8B Q4_K_M (`hymt2-1.8b`) and
CAT-Translate-1.4B Q4_K_M (`cat-translate-1.4b`) via Ollama CPU;
helper `huihui_ai/qwen3-abliterated:4b` (4k ctx — 8k OOMs, see REGRESSIONS.md).

## E01 — Engine choice for zh→en classical (Red Chamber ch3, 6 paras, A/D/F)

- Baseline: general 4B LLM, D terms 2/3, Harness ~70s.
- Hypothesis: translation-only MT model beats a general LLM as the engine.
- Result (AD9): Hy-MT2 D Harness 89.3, terms 3/4, book vote elects 黛玉→Daiyu.
- Verdict: KEEP. Hy-MT2 is the default engine (service.DEFAULT_TRANSLATE_MODEL).

## E02 — Glossary junk (Red Chamber)

- Baseline: glossary {Things, Small Boy, Gurl} enforced → Harness penalized, prose damaged.
- Interventions (all kept): miner stop-lists (speech verbs, kinship, servants…),
  junk-word/pinyin/echo/caps gates for ALL concepts, medial-cap flattening,
  enforce=false respected, kin-suffix rule.
- Result: AD5→AD9 glossary 2 names → 4-7 names, D terms 2/3 → 3/4.
- Verdict: KEEP. Gates are load-bearing; every new junk class gets a regression test.

## E03 — Transliteration lottery (Da Yu vs Daiyu, Yu Jun vs Yuzhen)

- Finding: helper and engine disagree on pinyin; enforcing either is a dice roll.
- Killed: standalone probe adoption (probes hallucinate: 雨村→Ujimura) — replaced by
  vote-candidates + `reconcile_glossary()` (book elects from in-context convergence).
- Added: `finalize_terms()` = elect + `normalize_renderings()` (rewrite recorded
  losers, never touch human-approved). Wired into `translate_project` (glossary feats only).
- Live proof: AD9 book vote {黛玉: Daiyu}; offline: finalize rewrites Yu Jun→Yucun.
- Verdict: KEEP. Open: unrecorded habit variants (Yu Village) still need translate-time retry.

## E04 — JA engine bake-off (ParallelFiction ja→en, 4 samples × 6 paras, bare)

- Hy-MT2 bare chrF: 41.18 / 44.96 / 59.78 / 43.62 (mean 47.4).
- CAT-1.4B bare chrF: 46.24 / 53.17 / 60.81 / 42.19 (mean 50.6). Wins 3/4.
- Published benchmarks agree (CAT-1.4B avg BLEU 33.7 ≈ TranslateGemma-12B territory).
- Verdict: CAT is the better ja→en ENGINE (kept as `cat` provider). Engine ≠ harness:
  CAT+harness collapsed to chrF 18-28 — see E05.

## E05 — CAT harness collapse (diagnosed → fixed architecturally)

- Symptom: same slice, CAT bare ~50 → harness arm chrF 18-28 (Hy-MT2 harness ≈ bare).
- Diagnosis: bare CAT fluent (4s/para); adding the "X must be translated as Y"
  glossary preamble hung generation >15min (killed by timeout). Narrow MT models
  go pathological on prompt machinery outside their training.
- Fix (general, not CAT-specific): `ChatProvider.prompt_features` — each ENGINE
  declares what it consumes in-prompt (Hy-MT2: all; CAT: bare only). Undeclared
  controls move OUTSIDE the prompt: guided retry + vote + normalize.
- Result: PENDING (CAT harness arm rerun).

## E08 — Phonetic authority (vendored Unihan kMandarin, 44k chars)

- Misread names were the top correctness leak: 黛玉=Da Yu, 雨村=Yu Jun,
  賈赦=Jiasha (elected!), probe Ujimura. Lists can't cover misreadings.
- `_pinyin_matches()`: DP aligning rendering words to chars' readings
  (polyphones accept any reading; descriptive words pass freely; chars may go
  unrendered for surname-drops like 林黛玉→Dai Yu; missing table data fails
  open; tone marks stripped). Wired three deep: `_is_bad_translation` rejects
  misreads for ALL flows, the probe cross-check keeps only valid surfaces as
  candidates and records misreads as normalize-losers, reconcile unions losers
  instead of replacing them.
- Vote distortion class closed too: verbatim-majority surface wins among
  norm-twins (Lady Wang, never Ladywang).

## E06 — Consistency machinery (drift score + backward-revision trigger)

- O2 answered with a number: `measure_drift()` = corpus-wide % of translated
  segments carrying the enforced rendering per term (100 = one voice).
  Recorded per translate run (`stats["drift"]`).
- O1 detection half: `review_confirmed_terms()` flags CONFIRMED terms whose
  rendering is the minority in shipped outputs (later chapters outvoted an early
  call) with the observed majority — adjudication input, never auto-applied.
  The application half exists (`mark_stale_by_term` + retranslation).
- Refinement verified safe: `guardrail_ok` rejects any polish that drops a
  glossary rendering, so normalize winners survive the refine pass.
- Open: O1 auto-adjudication policy (human queue vs model judge vs majority rule).

## E07 — Gold-glossary proof (OpenSakura ja→zh, Hy-MT2, 3 samples × 8 paras)

- Bare vs harness, scored against the dataset's own human glossary:
  terms bare 0/1, 1/4, 0/4 → harness 1/1, 4/4, 4/4 (9/9 vs 1/9).
- chrF mixed (26.0→27.4, 37.9→32.9, 24.6→27.6): references are GLM-4.7 MT
  output, so chrF rewards mimicking another machine — enforcing the HUMAN
  glossary correctly DIVERGES from it (sample 2). Terms + Harness Score are
  the honest metrics here, not chrF.
- Verdict: KEEP. First measurement where terminology counts, and the harness
  delivers it 9/9.

## E09 — Entity-first translation (structural consistency, not probabilistic)

- HYPOTHESIS: hinting names in the prompt can never reach 100% adherence —
  masking names as opaque proxies (⟦E3⟧) before the MT call and restoring
  after makes consistency structural.
- DG16 RESULT: D (hints) 13/13 vs G (placeholders) 10/13, chrF tied
  (41.79 vs 41.60). Root-caused, both fixed, no hypothesis change needed:
  (a) Hy-MT2 rewrites ⟦E4⟧→[E4] in flight and restore missed it — `[E{n}]`
  now restores (safe: never occurs in prose); (b) E09 exposed a deeper
  election bug (below) — placeholders were blamed for a restore gap.
- DG17 RESULT: D 13/13 (chrF 40.88), G 12/13 (chrF 41.61 — G now leads on
  fluency). Output-mining elected 'Lady Wang' from outputs that no component
  proposed (was: enforced 'Ladywang'). G's 3 residual misses root-caused:
  (a) a second rewrite surface `?E4?` (now restores); (b) '[Translation]'
  prefaces wasting tokens into truncation (now stripped in clean_output);
  (c) enforced-but-unused adjectives (Underground/Proper/Reason) eating
  proxies — unused entries now auto-ignore in reconcile. DG18 measures all
  three fixes.
- DG18 RESULT: D 13/13 (41.25), G 12/13 (41.65). G 31/44→37/44 segment
  adherence (?E? restore + preface strip working, leaks 0/0). Audit:
  'unused' adjectives (Underground/Proper/...) ARE used lowercase by the
  book — cheap hints, correctly spared by the prune rule. G's last misses
  are long-segment summarization drops + truncation (both legs truncate
  表兄) — E13's exact target. New QA error `proxy_remnant` (?E200?/[E4]
  can never be English) routes future occurrences to re-translation.
- DH19 runs D vs H (D + split_pieces=300) next.
- DH19 RESULT: **E13 PROVEN — H wins: chrF 43.0 vs 41.8 (+1.2), terms 13/13
  both, 613s vs 651s (shorter calls run faster on CPU).** Per-segment: H
  46/50, D 46/47 (per-leg glossaries differ slightly after finalize).
  `split_pieces=300` is now the production default. G Denouement: G reached
  12/13 + fluency lead; placeholders stay opt-in (config G) for the
  structural guarantee, H is the default path.

## Full-chapter-4 end-to-end (all fixes live)

- 108 chapters imported; ch4 (21 segments) translated 21/21, refine sample 5,
  QA 21 checked / 21 passed / 0 needs_review (1 warning). Whole-book
  glossary 10 enforced terms. Export validates structurally; the 107
  untranslated chapters remain source-language — honest partial-state
  reporting, not a bug (full-book CPU translation is days of compute; the
  per-chapter delivery path is what this proves).
- Rashomon long-form (CAT bare vs harness, 32 paras) runs next.

## E14 — Rashomon long-form ja→en (CAT bare vs harness, 32 public-domain paras)

- RESULT: both legs 32/32, zero CJK left. Glossary converged to one term
  (羅生門=Rashinmon — helper's reading; pro gold uses Rashomon/Rashōmon, so
  the human-review step owns this one; ja phonetic authority via Unihan
  kJapanese readings is queued work, not done).
- Harness wins: QA warnings 5→1, drift 80% (4/5 name hits; the 1 miss is a
  model early-stop at 1/5 of its token budget — NOT a budget cutoff, so no
  budget knob fixes it). Bare p0 shows why enforcement matters: CAT alone
  wrote mangled 'R?seimon'; the harness leg wrote clean 'Rashinmon'.
- Engine variance note: same CAT model read 暮方 as 'late afternoon' (bare)
  vs 'late at night' (harness) — the glossary shifts the draw; pro gold
  says 'dusk'. Unsupervised, unfixable at harness level; recorded.
- New QA warning `truncated_ending` (missing terminal punctuation on 60+
  char sources) catches early-stops that length ratios miss.

## E16 — Probe-seed dropped names (translator habit as election input)

- GAP: the helper drops some frequent names on EVERY attempt (no verdict, no
  candidates — election has nothing to elect; DH19 retries still dropped
  賈赦/雨村/迎春/惜春/母親/言語/老婆).
- FIX: post-retry, ignored terms with occ≥20, multi-char, no candidates get
  one translator standalone probe; transliteration-shaped, phonetic-gated
  renderings seed as vote candidates (status stays ignored; the book election
  promotes). Probe hallucinations (Ujimura) and function words never seed.
  Companion fix: fully-dropped batches now mark rows ignored (were left
  'suggested' forever); `_probe_title` preserves word breaks (Jia She).
- STATUS: unit-tested (55 green). DH20 measures glossary/terms vs DH19.

## E17 — De-hardcode: statistics + model judgment instead of word lists

- DELETED: `_STOP_WORDS` (~90 zh words), `_EDGE_STOP`/`_INNER_STOP` (~100
  chars), `_TIME_WORD`, `_JUNK_WORDS` (~150 en words), `_KIN_SUFFIX` (24 zh
  suffixes). They overfit the eval books and silently drop real terms in new
  books. What stays: structural patterns (number+counter, saying-verb
  regexes), structural signals (case, ALL-CAPS, echo, CJK-in-output), OUR OWN
  concept taxonomy, linguistic data (Unihan pinyin/romaji tables, verb-stem
  and honorific morphology), and three model/corpus judgments:
  (1) TF-IDF-ish mining rank (burstiness = the statistical signature of a
  term; df>80% grammar dropped on 10+ passage corpora),
  (2) translator-agreement gate (a second model must converge on a name
  rendering before it enforces; disagreement → vote candidates),
  (3) prompt-contract case signal (raw-lowercase + non-name concept = vote
  candidate, never enforced) + book election + demote-unused post-hoc.
- PROOF the architecture catches what the lists caught: phonetic authority
  rejects 薛姨媽=Xie Yimai (媽=ma≠mai) with no list; agreement demotes Small
  Boy/Brother/Aunt-Xue-disagrees; dispersion drops 的.
- STATUS: 123 offline green. Excerpt proof (DH21 vs DH20: junk-enforced
  count, terms, chrF) runs when E17 is declared done — not before.
- E17 DECLARED DONE: dead lists deleted from code (grep-clean), 123 green,
  REGRESSIONS rows updated to the new mechanisms. DH20 baseline (E16 code,
  D-leg): glossary=13, 20/20 segs. DH21 (E17, same slice, DH legs) measures
  the de-hardcoded pipeline head-to-head.
- DH21 RESULT: glossary 13→6, terms 13/13→5/6, H chrF 43.0→42.87. Two distinct
  causes: (1) determiner-only disagreement — the probe's 'the old lady' vs the
  helper's 'Old Lady' demoted 老太太-class names (FIXED: `_norm_pinyin` strips
  a leading article everywhere; articles are grammar, closed-class);
  (2) agreed common nouns (Sister/Madam/Master/Jade said) now enforce when
  both models agree — OPEN: trivially-true enforcement, glossary noise, no
  measured damage yet; a list-free distinctiveness rule only if DH22 shows
  harm. Seed probes were all correct readings (Baoyu/Jia Mu/Daiyu); the book
  vote elected 黛玉=Daiyu. DH22 measures the article fix.
- DH22 RESULT: glossary UNCHANGED (same 6; 王夫人/老太太 still canon={} — they
  were never classified, not disagreed: batch dice dropped them and retry's
  raw-occurrence order spent all 12 solo slots on diffuse words). chrF still
  rose (D 41.26→41.78, H 42.87→43.47 — new best) and the vote elected 賈政 too.
  FIX: retry spends diffuse-last (df>80% computed over the book) so bursty
  names take the solo slots — same statistic as mining, no lists. DH23 proves
  it live (expect 王夫人/老太太 classified: rescued or vote-seeded).
- DH23 (killed after D-leg replicated DH22: 41.85, 5/6): the book-wide df
  threshold changed nothing — the retry 12 were identical, because nothing
  exceeds 80% of 4456 book segments. Diagnosis from the live log, fixed in
  the same pass: dispersion must scope to the translation window (passages
  holding any pool form), not the book. DH24 proves it.
- DH24 forensics (pool dump from the live DB): window=57 passages, max df
  0.37 — NO cutoff rule can fire, so slots go by burstiness (count x
  log(N/df)), same weight as mining, retry_limit 12→20. Verified by hand on
  the live pool: 老太太 ranks #10, 王夫人 #15 — both inside the 20. DH25
  measures.
- DH25 RESULT: 20 burstiness-ordered solos ran; 老太太 RESCUED, 王夫人 earned
  its first solo slot (helper dice still dropped it). Glossary 6→7, terms
  5/6→6/7 both legs, D chrF 42.55 (new D best), and the book vote elects all
  three great names (黛玉=Daiyu, 王夫人=Lady Wang, 老太太=Old Lady). The
  end-to-end loop (slot → classify-or-seed → elect → normalize) is proven.
- Refine micro-probe (3 Red Chamber sentences, Hy-MT2 + helper refine):
  tense/punctuation-only edits, chrF 67.7→67.2 / 48.5→48.8 / 53.2→53.2,
  terms untouched. The old DH20 "E +refine 18.75" was a stale config artifact,
  not the current path (glossary-fed prompt + term/number/length guardrails).
  VERDICT: refine is safe polish, not a quality lever — keep opt-in, never in
  the scored pipeline by default.

## E18 — Comics on real pages (Korean OCR + real-page loop)

- Korean OCR with zero new packages: bundled RapidOCR takes a config
  override, so only weights were fetched to D: (official Korean PP-OCR rec
  3.3MB + dict). `images.py` picks the rec model per project language.
  Control: default engine reads 1 garbage line on Korean text; ko finds all
  4 content lines — but PP-OCRv1-mobile misreads ~1 syllable in 4
  (돌아왔다→아왔다). Regression test pins detection + clean phrases and names
  the weakness; upgrade = newer ko rec weights, not code.
- Real story pages won via Asura (static HTML, no wall): Nano Machine ch.1
  EN pp.2–3, 11/11 dialogue blocks OCR'd off webtoon art (spacing slips like
  MYLEG kept — real lettering). The pages caught a live bug: fragmentary
  screams ('DAMN IT ALL...') make Hy-MT2 echo the source with '=>' notes and
  a [来源文本] marker (100-char dump into one bubble). Fixed in
  `clean_output` (echo-marker strip, legit '=>' untouched) + regression test
  with the exact string; re-run on the same pages stores 该死！！. README now
  shows downscaled credited crops (raw | ours), not mockups.
- NOT done: same-chapter ZH raw (MangaDex hosts no Chinese for this title;
  Baozimh/Comick bot-walled) and Korean story pages (Nano Machine: covers
  fetch fine, chapters render in JS — needs Playwright or a manual drop).
  Stylized cover lettering defeats the v1-mobile model (0 lines on two real
  covers — brush strokes + vertical text, a documented OCR limit, not a
  regression). Hy-MT2 ko→en and ja→en smoke-tested clean (2s/1s).
  Fetch protocol documented in DATASETS.

- zh got kMandarin DP alignment; ja kanji names had NO phonetic check (the
  pinyin clause even misfired on romaji-shaped renderings — fixed by scoping
  each clause to its source_lang). Vendored `romaji_table.py` (13,395 kanji).
- `_romaji_matches`: strict full-consumption DP over kun/on readings with
  rendaku voicing (TA→DA), long-vowel folds (TOUKYOU→TOKYO, OO→O),
  verb/adj stem truncation (TAKAI→TAKA, WATARU→WATA), honorific stripping,
  and anchored-ends fallback for ateji surnames (渡辺 Watanabe).
- 18-pair prototype: 18/18 (12 valid incl. Honda/Tokyo/Osaka/Takahashi/
  Watanabe, 6 misreads rejected incl. Yamamoyo/Murakama/Watanaba).
- STATUS: unit-proven, live-unproven (bake-off glossaries hold no
  romaji-shaped helper renderings to re-judge). Next PFiction/OpenSakura run
  must compare glossary sizes vs history — a false reject shrinks the
  glossary, which is the monitored regression signal.
- LIVE-PROVEN 12-name direct test: helper classified 田中…松本; gate kept 11
  correct (Tanaka…Yoshida incl. Takahashi/Watanabe/Osaka) and rejected exactly
  one — 松本=Matsubara, a REAL helper misread (松本 is Matsumoto; Matsubara
  is 松原). 11/11 keeps correct, 1/1 rejects wrong. PFiction slices stay
  name-poor (titles only), so this direct probe is the promotion evidence.
- E10 — Output-mined winner election (the deeper bug): D's glossary enforced
  `王夫人=Ladywang` while every output said 'Lady Wang' — the winner could
  only come from recorded candidates, but the natural surface was one NO
  component proposed. `reconcile_glossary()` rewritten: candidates define the
  reading bucket (letters-only), the WINNER is the bucket's most frequent
  verbatim phrase mined from the term's own segments (`_latin_phrases`,
  per-segment counting so one repetitive paragraph can't elect). Same-norm
  verbatim twins ('Ladywang') become normalize-losers. Dorothy Dunne pair
  ('Jia Shai', 'Rui Ha' in D outputs) noted as model-side errors for the
  placeholder track to absorb structurally.

## E11 — Round-trip quality estimation (unsupervised per-segment signal)

- `estimate.py`: `roundtrip_chrf` + `entity_recall` (do source terms reappear
  in the en→zh back-translation?). Pure functions, unit-tested with canned
  pairs. Validation (correlation vs term adherence/QA on shipped outputs)
  and routing (escalate/refine) only if the signal predicts failures.
- Dataset: Rashomon banked (Gutenberg ja 32 paras + 3 pro-annotated pairs);
  full-run script ready (`D:\StoryTranslation_data\tmp\run_rashomon.py`: CAT
  bare vs harness, terms/QA/drift) — launches after DG17. Wording full text
  is gated; Haruhi EN fan text is EN-only (no ja source) → style reference,
  not parallel eval.

## E12 — Forward-backward revision (future context fixes the past)

- DESIGN (unvalidated): chapter N ships with placeholders; after chapter N+1
  translates, a bounded re-read of N's segments with N+1's entities resolves
  first-mention ambiguities (pronoun gender, title-vs-name). Validation:
  does re-reading change anything measurable (drift/terms), or is the first
  draft already at ceiling? Queued after DG18.
- VALIDATION RESULT: **shelved, 0/19 with a working probe.** First probe
  returned 0/19 but was INVALID (parse_json_array keeps dicts only; the
  helper answers string arrays — every reply silently discarded). Retry with
  a one-line schema (NO-CHANGE vs CHANGE: a -> b | why) got clean answers
  citing passage content ('Phoenix Pepper→Wang Xifeng' context, 'Baoyu')
  and still 0/19 changes. Lesson: validate the probe before the hypothesis.
  Revisit on harder material (gender-ambiguous first mentions); scripts kept
  at D:/StoryTranslation_data/tmp/e12_probe*.py.

## E13 — Piecewise long-segment translation (change the unit, not the words)

- HYPOTHESIS: clause drops + truncation on long paragraphs (DG17 G-1, D/G
  表兄-truncation) come from oversized single MT calls. Translating sentence
  pieces that share one kw (glossary hits from the FULL segment) keeps names
  consistent while each call stays in the model's comfort zone.
- STATUS: `split_long()` + `translate_project(split_pieces=N)` built,
  unit-tested (rejoin lossless, DB granularity unchanged). Live proof runs
  as config H (D + split) vs D in DG19 after DG18.

## Open questions (not yet experiments)

- O1 backward revision: later chapters change earlier meaning (e.g. 國/外戚 reveals).
  Current pipeline never revisits. Needs: translation-state tracking + retranslate pass.
- O2 consistency metric: cross-chapter name drift has no number; build drift detector first.
- O3 gold-glossary eval mode: OpenSakura ships per-row glossaries — measure enforcement
  against human terminology, not just chrF.
- O4 dataset upgrade: GuoFeng V1 (pro zh→en web-fiction, chapter-aligned) needs manual
  registration download; Baka-Tsuki (ja→en long-form fan) needs a scraper.
- O5 fine-tuning: LoRA on (source, glossary, reference) triples from GuoFeng/OpenSakura
  so the ENGINE consumes glossary natively — only if E05 shows prompting can't.
