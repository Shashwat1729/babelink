# Datasets

## Evaluation format (spec §49)

One JSON object per line:

```json
{"id":"1","source_language":"zh","target_language":"en","source":"...","reference":"...","context":"...","genre":"cultivation","domain":"web_novel"}
```

## Running a benchmark

```bash
storytranslate benchmark-run data/eval.jsonl --provider ollama --model huihui_ai/qwen3-abliterated --out leaderboard.md
```

## Legal posture (spec §46, §54)

Every dataset must record license fields before use. Redistribution requires
`allowed_for_redistribution`. Do not bundle copyrighted references. The
"King of Ten Thousand Years" case study is metadata + user-provided material only.

## Sources to integrate later

OPUS, WMT, FLORES, Hugging Face Datasets, Project Gutenberg (public domain,
jurisdiction-dependent).
