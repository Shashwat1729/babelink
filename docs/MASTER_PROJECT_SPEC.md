# Context-Aware Multilingual Story Translation Engine — Master Project Specification

> This document is the source of truth for architecture, objectives, and requirements.
> Refer to it before any major architectural decision. Update it when intentional
> decisions change; record deviations in `DECISIONS.md`.

## 1. CORE PROBLEM

Current machine translation systems often perform poorly on long-form fiction because they treat translation as an isolated sentence-level problem.

Typical bad workflow:

Chapter → Split into chunks → Translate chunk → Translate next chunk → Merge

This causes:

- terminology drift
- inconsistent names
- incorrect pronouns
- incorrect speaker identification
- literal translations
- unnatural English
- broken dialogue
- inconsistent cultivation terminology
- inconsistent titles / locations
- loss of story context
- incorrect word meanings
- awkward sentence structures
- repeated terminology changing between chapters
- failure to understand references to previous events
- poor literary quality

The system being built must solve these problems structurally.

## 2. FUNDAMENTAL DESIGN PRINCIPLE

Do NOT build: source sentence → translation model → translated sentence.

Instead build:

SOURCE → DOCUMENT INGESTION → STRUCTURAL ANALYSIS → STORY UNDERSTANDING →
STORY BIBLE → ENTITY RESOLUTION → TERMINOLOGY → TRANSLATION MEMORY →
CONTEXT RETRIEVAL → FIRST-PASS TRANSLATION → LITERARY REFINEMENT →
QUALITY ASSURANCE → HUMAN REVIEW → FINAL OUTPUT

The important unit is not «sentence». It is «story segment interpreted within a persistent world.»

## 3. PROJECT PHILOSOPHY

The final system should behave like a combination of: Translator + Literary editor + Terminologist + Continuity editor + Proofreader + Story knowledge base.

These responsibilities should be separated into software stages so each can be tested and improved independently. Do not attempt to solve everything with one giant LLM prompt.

## 4. LOCAL-FIRST ARCHITECTURE

Users should be able to run the translation system locally without paying API costs.

Support, where practical: CPU inference, NVIDIA CUDA, AMD GPU, Apple Silicon / Metal, Vulkan, llama.cpp, GGUF, Ollama, vLLM, Hugging Face Transformers, OpenAI-compatible local endpoints, other local inference backends.

Commercial APIs are optional. No commercial API should be mandatory.

## 5. DYNAMIC MODEL SELECTION

Users select their own models, but the application recommends models based on detected hardware and task requirements: CPU, cores, RAM, GPU, VRAM, unified memory, OS, inference backend, quantization, context window, language pair, genre, desired quality, desired speed, model specialization.

Recommendation must remain optional; the user can always override it.

## 6. DO NOT ASSUME THE LARGEST MODEL IS BEST

Compare and route across:

- **Translation-specialized models** — built/fine-tuned for translation, specific pairs, literary translation.
- **General-purpose multilingual LLMs** — story analysis, entity extraction, reasoning, ambiguity resolution, literary editing, difficult passages.
- **Small models** — NER, language detection, terminology extraction, classification, segmentation, QA.

## 7. MODEL REGISTRY

Each model carries metadata: name, provider, backend, languages, capabilities (translation, literary_editing, reasoning, ner, long_context), recommended_tasks, memory_requirements (ram_gb, vram_gb), quantizations.

Support adding models from Hugging Face, GGUF repos, Ollama, local OpenAI-compatible endpoints, custom definitions. Do not hardcode a small fixed list.

## 8. HARDWARE DETECTION AND BENCHMARKING

Detect hardware. Optionally benchmark: model loading time, prompt processing speed, generation speed, tokens/sec, RAM/VRAM usage, CPU/GPU utilization. Benchmark must be optional, cancellable, reasonably lightweight. Use results in recommendations.

## 9. MODEL ROUTING

Different models for different tasks (NER → 3B local, translation → specialized 7B/8B, difficult context → 14B, refinement → stronger LM, QA → small model + deterministic checks). Manual config or presets: Auto / Fast / Balanced / Quality / Maximum Quality.

## 10. CONFIDENCE-BASED MODEL ESCALATION

Easy segment → small/fast model → high confidence accept; medium → secondary validation; low → stronger model; critical contradiction → human review. Confidence from meaningful signals, not blind self-reported confidence.

## 11. STORY BIBLE

Central component. Per project:

```
story_bible/
    characters.json
    locations.json
    organizations.json
    terminology.json
    items.json
    abilities.json
    relationships.json
    timeline.json
    factions.json
    world_rules.json
    style_guide.json
    chapter_summaries.json
    arc_summaries.json
```

Editable by the user. AI-generated info has status: inferred / uncertain / suggested / confirmed / user-confirmed. Never silently turn uncertain information into confirmed canon.

## 12. CHARACTER DATABASE

Track: source names, canonical name, aliases, nicknames, titles, gender where known, pronouns, relationships, personality, speech style, social status, rank, faction, occupation, first appearance, notes.

## 13. ENTITY RESOLUTION

Resolve entities and concepts; never treat every source phrase independently. Status: confirmed same / likely same / possibly same / different / unknown. Never silently merge uncertain entities.

## 14. LANGUAGE-INDEPENDENT ENTITY MODEL

Internal canonical entity is not English-dependent. `cultivation_realm_001` maps to Chinese 炼气期, English "Qi Refining Stage", Japanese/Spanish… Same for characters, locations, organizations, items, skills, titles, concepts. Critical for multilingual support.

## 15. TERMINOLOGY DATABASE

Structured entries: id, source_forms, concept, canonical_translation per language, policy, status. Policies: fixed / preferred / flexible / contextual / do-not-translate / transliterate / preserve-original / user-defined.

## 16. TERM IDENTITY VS SENTENCE TRANSLATION

Canonical terms stay consistent; sentences stay natural. Terminology maps canonically but is not blindly dictionary-substituted.

## 17. TWO TYPES OF VOCABULARY

Separate canonical terminology (names, locations, sects, ranks, realms, skills, artifacts, bloodlines, concepts — stable) from flexible prose vocabulary (said/replied/whispered… — natural variation allowed).

## 18. NAMES WITH MEANING

Determine whether a phrase is: personal name, title, nickname, epithet, organization, location, descriptive phrase, deliberately meaningful name/wordplay. Policies: transliterate / translate / hybrid / preserve-original / contextual.

## 19. TITLES AND HONORIFICS

Treat titles separately from names (Lin Fan / Brother Lin / Senior Brother Lin / Young Master Lin / Elder Lin / Sect Master). Understand relationships between these expressions.

## 20. CULTIVATION / FANTASY TERMINOLOGY

Configurable genre profiles, not hardcoded logic: realms, stages, breakthrough, qi, meridians, dantian, techniques, martial arts, sects, clans, treasures, artifacts, bloodlines, spiritual beasts, heavenly tribulations, ranks, titles. Structured hierarchy (Body Tempering → Qi Refining → Foundation Establishment → Core Formation → Nascent Soul). Terminology should understand relationships between concepts.

## 21. TRANSLATION MEMORY

Store: source segment, source context, translation, chapter, scene, entities, terminology, model, timestamp, confidence, human-approved status. Reference, not blind string substitution.

## 22. CONTEXT HIERARCHY

GLOBAL (Story Bible) → BOOK/SERIES (arc info) → CHAPTER (summary) → SCENE (summary) → LOCAL (nearby paragraphs). Retrieve dynamically; never send the whole novel to every request.

## 23. CONTEXT RETRIEVAL

Hybrid: keyword match + entity match + semantic similarity + recency + canonical importance + scene relevance. Retrieve only the most relevant information.

## 24. PROGRESSIVE STORY UNDERSTANDING

Story Bible evolves across chapters; supports updating knowledge as the story develops.

## 25. CONTRADICTION DETECTION

Conflicting evidence → flag "Potential contradiction detected." Never silently overwrite.

## 26. STORY SUMMARIES

Generate and store: chapter summaries, scene summaries, arc summaries, important events, new characters, new locations, new terminology, relationships, unresolved threads.

## 27. TIMELINE

Optionally track: events, chapter, characters involved, location, relative time, consequences.

## 28. TRANSLATION PIPELINE

Input → language detection → document parsing → chapter segmentation → scene segmentation → entity extraction → terminology extraction → Story Bible retrieval → translation memory retrieval → context assembly → first-pass translation → literary refinement → deterministic QA → AI QA → human review if needed → output. Every stage modular.

## 29. FIRST-PASS TRANSLATION

Priorities: meaning, facts, entity identity, terminology, speaker identity, pronouns, numbers, time relationships, narrative structure. Do not sacrifice meaning for beautiful prose.

## 30. LITERARY REFINEMENT

Second pass improves prose, rhythm, dialogue, paragraph structure, awkward literal constructions, repetition, readability. Must NOT change canonical names/terminology, invent/remove facts, change relationships/gender/speaker identity/numbers/chronology, or invent events.

## 31. STYLE GUIDE

Per project: narrative formality, dialogue style, sentence length, contractions, punctuation, capitalization, narration/action style, humor, archaic language, terminology preferences. Presets: Literal / Faithful Natural / Literary / Highly Literary / Custom.

## 32. GENRE PROFILES

Xianxia/Cultivation, Wuxia, Xuanhuan, Fantasy, Romance, Slice of Life, Mystery, Thriller, Horror, Historical, Science Fiction, LitRPG, Game/System, Comedy, Drama. Influence terminology extraction, context retrieval, style, model recommendation, QA. Engine remains genre-independent.

## 33. MULTILINGUAL LANGUAGE PROFILES

Per language pair: segmentation rules, punctuation, honorifics, name handling, transliteration, quotation conventions, grammar considerations, writing system, romanization. No English assumptions in the core.

## 34. DIALOGUE ATTRIBUTION

Identify likely speakers via dialogue structure, nearby context, narrative tags, character presence, speech style, scene info. If uncertain, flag with speaker confidence instead of inventing.

## 35. FUTURE CONTEXT

Future text may disambiguate earlier text, but must not leak spoilers. For disambiguation, not rewriting.

## 36. DETERMINISTIC QA

Check: missing/duplicate paragraphs, missing chapters, untranslated source, incorrect numbers, changed names/terminology, broken HTML, malformed EPUB, missing dialogue, suspicious omissions, accidental additions, formatting errors.

## 37. AI QA

Semantic judgment checks: meaning preservation, pronouns, speaker identity, relationship consistency, suspicious literal translation, terminology inconsistency, awkward prose, contradictions, omissions, hallucinations. Return structured findings. Never silently rewrite.

## 38. HUMAN-IN-THE-LOOP

Users edit translations, terminology, characters, locations, relationships, style, prompts, summaries, Story Bible. Corrections scoped as local / project / global. Do not automatically propagate every edit everywhere.

## 39. SELECTIVE RETRANSLATION

Term change → affected-segment count → offer retranslate of affected segments only.

## 40. TRANSLATION STATE

UNPROCESSED / ANALYZED / TRANSLATED / REFINED / QA_PASSED / NEEDS_REVIEW / HUMAN_APPROVED / STALE.

## 41. CRASH SAFETY

Checkpoint; save after chapters/segments; resume automatically; preserve model responses, Story Bible, QA, edits. Never require restarting from chapter 1.

## 42. API COST TRACKING

External APIs: input/output tokens, cost, chapter cost, project cost, words translated, estimated cost. Allow budget limits.

## 43. LOCAL INFERENCE OPTIMIZATION

Quantization, GPU/CPU offloading, batching, KV cache reuse, prompt caching, context management, concurrency, speculative decoding, model warm-up, model unloading. Reliability over tiny perf gains.

## 44. MODEL BENCHMARKING

First-class evaluation: terminology consistency, names, pronouns, speaker attribution, literary quality, meaning preservation, long-context consistency, genre terminology, hallucination, omissions, chapter coherence, speed, memory.

## 45. DATASET STRATEGY

Sources: Hugging Face Datasets, OPUS, WMT, FLORES, public-domain books, openly licensed novels/fiction, multilingual datasets, literary datasets, user-provided material, MT samples, human translations, genre-specific datasets. Do not assume every online dataset is legally reusable.

## 46. DATASET LICENSE TRACKING

Record: name, source_url, creator, license, languages, domain, size, description, download_date, license_verified, redistribution/training/evaluation flags. Status: Evaluation-only / Training permitted / Redistribution permitted / Internal use only / License unclear / Do not use. Never auto-redistribute unclear rights.

## 47. HUGGING FACE INTEGRATION

`storytranslate dataset search/inspect/license/import/benchmark` workflows; same system supports local datasets.

## 48. DATASET ADAPTERS

DatasetAdapter → HuggingFaceAdapter / OPUSAdapter / WMTAdapter / FLORESAdapter / LocalFileAdapter / CustomDatasetAdapter. One internal normalized format.

## 49. COMMON EVALUATION FORMAT

Entries: id, source_language, target_language, source, reference, context, genre, domain, entities, terminology, metadata.

## 50. DATASET CATEGORIES

General translation, literary translation, long-context translation, terminology consistency, character consistency, genre-specific subsets.

## 51. HUMAN TRANSLATION VS MTL DATASET

SOURCE → BAD MTL → HUMAN/HIGH-QUALITY TRANSLATION → ERROR ANNOTATIONS. Record error categories, severity, explanation, affected entity/terminology, required context.

## 52. ETERNAL KING / KING OF TEN THOUSAND YEARS CASE STUDY

Representative MTL-failure case study. Verify rights before including any actual source/reference text. If redistribution not permitted: user-provided material, metadata/references only, legally permitted samples. NOT to be confused with "Eternal Reverence".

## 53. MTL FAILURE CORPUS

Dedicated corpus of Source / Bad MTL / Better translation / Error categories / Explanation / Context required. Grows over time; user bugs become regression examples when legally usable.

## 54. DATASET ACQUISITION POLICY

discover → inspect license → verify → import permitted material. Never "download everything".

## 55. PUBLIC-DOMAIN MATERIAL

Project Gutenberg etc. for EPUB processing, long-form translation, literary testing, regression testing. Verify jurisdiction/rights before redistribution.

## 56. USER-PROVIDED MATERIAL

Users import their own EPUB/TXT/DOCX/HTML/images/manga. Not required to redistribute.

## 57. BENCHMARK PIPELINE

Experiments: A) bare LLM, B) +glossary, C) +Story Bible, D) +translation memory, E) +hierarchical context, F) full pipeline + refinement + QA. Measure the actual improvement from every layer.

## 58. BENCHMARK METRICS

Multi-dimensional, configurable weights: meaning preservation, terminology consistency, entity consistency, narrative coherence, literary quality, dialogue quality, formatting preservation, hallucination, omission, speed, memory. Not only BLEU/ROUGE/COMET.

## 59. HUMAN EVALUATION

UI: SOURCE / REFERENCE / MODEL A / MODEL B / MODEL C with Meaning, Naturalness, Terminology, Character voice, Dialogue, Overall; pairwise A better / B better / Equal. Store results.

## 60. MODEL LEADERBOARD

Local leaderboard, ranked separately by language pair, genre, hardware, task, quality/speed tradeoff. No universal best model.

## 61. MODEL RECOMMENDATION

Best overall / best local / best CPU-only / best low-VRAM / best translation-specialized / best literary / best speed / best per language pair. Based increasingly on project benchmark results.

## 62. CONTENT HANDLING

Translation/transformation tool: faithfully translate legitimate user-provided fictional content. No explicit details not in source; no amplification toward more extreme material; do not intentionally make content harmful.

## 63. MODEL REFUSAL HANDLING

Detect refusal → determine whether ordinary translation → if appropriate retry with neutral translation-focused prompt → route to appropriate translation model → allow alternate model → report failed segment clearly. No mechanisms to defeat provider safety systems; no unnecessary moralizing or altering legitimate source content.

## 64. PROMPT ARCHITECTURE

Compose from: System instructions + Language profile + Genre profile + Style guide + Story Bible + Terminology + Character context + Translation memory + Retrieved context + Current source. Each component separately versioned.

## 65. PROMPT VERSIONING

Record model, model version, quantization, prompt version, language profile, genre profile, Story Bible version, terminology version, translation memory version, sampling parameters.

## 66. REPRODUCIBILITY

If output changes between runs, identify why: model/prompt/Story Bible/terminology/TM/context/sampling/backend changes.

## 67. SAMPLING

Translation defaults favor consistency: temperature, top-p, top-k, repetition penalty, seed where supported. Relatively deterministic defaults.

## 68. EPUB SUPPORT

Preserve: chapters, headings, paragraphs, italics/bold/emphasis, images, footnotes, metadata, TOC, cover, CSS. Output: translated EPUB, bilingual EPUB, original + translation.

## 69. MANGA / MANHUA / MANHWA SUPPORT

Separate adapter: IMAGE → OCR → text-region detection → panel ordering → speaker/context analysis → Story Bible → translation → inpainting → typesetting → output. Reuse Story Bible, terminology, entities, TM between novel and comic versions.

## 70. WEB NOVEL SUPPORT

HTML, local saved HTML, permitted URL imports, custom importers. Scraping specific sites is not the core architecture. Importer → EPUB / TXT / DOCX / HTML / Manga.

## 71. PLUGIN ARCHITECTURE

Replaceable interfaces: LLMProvider, InferenceBackend, ModelRegistry, Importer, Exporter, Tokenizer, NERModel, TranslationEngine, ContextRetriever, VectorStore, GlossaryStore, TranslationMemory, QAEngine. No vendor lock-in.

## 72. DATABASE

SQLite initially. Tables: projects, documents, chapters, scenes, segments, characters, entities, locations, organizations, terminology, relationships, translations, translation_memory, context_records, summaries, qa_results, model_runs, human_edits, style_guides, prompt_versions, datasets, dataset_samples, benchmarks.

## 73. VECTOR SEARCH

Local: FAISS / LanceDB / Chroma / SQLite vector extensions / lightweight local implementation. Must work without a complex vector DB for small projects.

## 74. DESKTOP UI

Cross-platform desktop preferred; Windows, Linux, macOS. Areas: Project, Chapters, Translation, Story Bible, Characters, Terminology, Translation Memory, QA, Models, Benchmarks, Datasets, Settings.

## 75. TRANSLATION WORKSPACE

SOURCE / TRANSLATION / CONTEXT panels plus characters, terminology, previous translation, QA. Side-by-side editing.

## 76. TERMINOLOGY UI

Source term, canonical concept, preferred/alternative translations, type, notes, status, occurrences. Actions: lock, unlock, merge, split, rename, ignore.

## 77. STORY BIBLE UI

Edit characters, locations, organizations, items, skills, titles, relationships, world rules, events. Distinguish AI suggestion / confirmed / user confirmed / uncertain.

## 78. IMPORT WORKFLOW

Import novel → detect language → parse → chapters → scenes → metadata → entities → initial terminology → initial Story Bible → chapter summaries → user review → begin translation. No manual entry of hundreds of terms.

## 79. PROGRESSIVE TERMINOLOGY

Unknown recurring term → occurrences count → possible type → suggested translation → Accept/Edit/Ignore. Accepted terms join Story Bible/terminology.

## 80. CONTEXT BUDGET

Rank by canonical importance + current relevance + entity overlap + recency + semantic similarity. Only highest-value context within budget.

## 81. LONG-CONTEXT STRATEGY

structured facts + summaries + retrieval + recent context rather than dumping the entire novel into every request.

## 82. CHAPTER SUMMARIES

Plot events, new characters, locations, terminology, revelations, relationships, unresolved threads.

## 83. ARC SUMMARIES

Book → Arc 1..N with higher-level summaries for long-range continuity.

## 84. OUTPUT VALIDATION

Validate EPUB, XML/HTML, chapter count, segment count, missing translations, malformed markup, formatting preservation, EPUB validation tools. Never silently produce corrupted output.

## 85. CLI

`storytranslate import/analyze/glossary/translate/qa/export/benchmark/dataset` — headless operation.

## 86. CONFIGURATION

Readable YAML-style config: language source/target, translation mode, models per task, context sizes, terminology enforcement, QA thresholds.

## 87. PRIVACY

Local-first; documents remain local by default. Clearly indicate when data leaves the machine. No mandatory cloud account.

## 88. OPEN-SOURCE LICENSE MANAGEMENT

Inspect repo/dependency/model/dataset/redistribution licenses before use. Maintain third-party attribution. No copying merely because publicly visible.

## 89. EXISTING PROJECT RESEARCH

Inspect: LN-Translator, Glossarion, TranslateBooksWithLLMs, ebook_translater, epublate, ln-epub-translator, OrionTranslator, XianScan, Rainman / translate-book, others. Compare license, architecture, language support, local model support, glossary, Story Bible, TM, context, UI, EPUB, manga, model routing, QA, benchmarking, activity. Fresh code and license audit before selecting a foundation.

## 90. POSSIBLE FOUNDATION PROJECTS

- **LN-Translator** — light novels, EPUB/TXT/DOCX/HTML workflows.
- **Glossarion** — multilingual/local-model/manga workflows.
- **TranslateBooksWithLLMs** — book translation, glossary, context, refinement.
- **ebook_translater** — human-in-the-loop, local LLM workflows.
- **epublate** — lore-bible/project-level context.
- **OrionTranslator** — local inference, NER, glossary generation, model/backend selection.
- **XianScan** — manhua/manga OCR, translation, typesetting.
- **Rainman/translate-book** — agent orchestration, resumability, validation.

Determine which provides the strongest foundation after a fresh audit.

## 91. DEVELOPMENT PHASES

- **Phase 1 — Core:** EPUB/TXT, chapters, multilingual, local LLM, API abstraction, glossary, TM, resumable translation, EPUB output.
- **Phase 2 — Story Intelligence:** Story Bible, characters, entities, terminology, summaries, context retrieval, relationships.
- **Phase 3 — Quality:** two-pass translation, literary refinement, deterministic QA, AI QA, selective retranslation, human review.
- **Phase 4 — Local ML:** hardware detection, model registry, recommendation, benchmarking, routing, specialized translation models.
- **Phase 5 — Evaluation:** HF integration, dataset registry, OPUS/WMT/FLORES adapters, MTL failure corpus, benchmark runner, leaderboard, human evaluation, regression suite.
- **Phase 6 — Comics:** OCR, text detection, panel detection, context, inpainting, typesetting.

## 92. REGRESSION TESTING

Every important bug becomes a regression test with explicit expected invariants.

## 93. EVALUATION-DRIVEN DEVELOPMENT

Every significant architecture change evaluated against fixed datasets: baseline vs +glossary vs +Story Bible vs +TM vs full context vs full + refinement + QA.

## 94. REPOSITORY STRUCTURE

```
story-translate/
├── apps/{desktop,cli}
├── core/{projects,documents,segmentation,entities,terminology,story_bible,translation_memory,context,translation,refinement,qa,evaluation,export}
├── models/{registry,detection,routing,benchmarking}
├── providers/{llama_cpp,ollama,vllm,transformers,openai_compatible}
├── importers/{epub,txt,docx,html,manga}
├── exporters/{epub,html,txt,bilingual}
├── profiles/{languages,genres,styles}
├── datasets/{manifests,evaluation,regression}
├── tests/{unit,integration,regression,evaluation}
├── docs/{MASTER_PROJECT_SPEC.md,ARCHITECTURE.md,ROADMAP.md,DECISIONS.md}
└── scripts/
```

## 95. DOCUMENTATION REQUIREMENT

README.md, docs/{MASTER_PROJECT_SPEC.md, ARCHITECTURE.md, ROADMAP.md, DECISIONS.md, MODELS.md, DATASETS.md, DEVELOPMENT.md} covering architecture, setup, local models, APIs, datasets, licensing, benchmarks, development, contribution, limitations.

## 96. ARCHITECTURE DECISION RECORDS

Record significant decisions in docs/DECISIONS.md.

## 97. FIRST IMPLEMENTATION TASK

Before substantial application code:

1. Save this entire specification. ✅
2. Inspect existing GitHub projects.
3. Compare licenses.
4. Compare architectures.
5. Identify reusable components.
6. Determine fork vs clean implementation.
7. Produce architecture document.
8. Produce roadmap.
9. Produce dependency/license audit.
10. Produce initial model/dataset strategy.
11. Only then begin implementation.

## 98. MVP ACCEPTANCE CRITERIA

Import EPUB → detect language → extract chapters → build initial Story Bible → extract terminology → select/recommend local model → translate using context → maintain terminology → run QA → human correction → resume later → export translated EPUB. No manga support in MVP; novel workflow first.

## 99. FINAL ACCEPTANCE CRITERIA

Consistency (names/locations/organizations/cultivation levels/skills/titles), Context (pronouns/speakers/ambiguity/prior events), Quality (natural prose, not sentence-level MTL), Fidelity (no invented/removed content, preserved events/names/terminology), Local operation, Flexibility (model selection/recommendation/routing, optional APIs), Reproducibility (trace model/version/prompt/Story Bible/glossary/TM/context/settings), Maintainability (modular).

## 100. ULTIMATE PROJECT GOAL

Judged by whether it can translate an entire long-form story while maintaining coherent understanding across hundreds of chapters — not by winning a single-sentence bake-off. The objective is NOT "find one magical LLM" but "build a translation architecture where even relatively small local models produce significantly better long-form fiction translation because the system gives them persistent story knowledge, terminology, context, translation memory, specialized routing, iterative refinement, and rigorous QA."

## 101. FINAL DEVELOPMENT RULE

Repeatedly ask: «"Does this implementation actually improve long-form story translation, or are we merely building another generic LLM wrapper?"» If the latter, reconsider the design. Always refer back to this document.
