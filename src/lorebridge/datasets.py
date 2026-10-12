"""Dataset adapters + common evaluation format (spec §48, §49)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol


class DatasetAdapter(Protocol):
    def load(self) -> list[dict]:
        ...


REQUIRED = ["id", "source_language", "target_language", "source", "reference"]


def normalize_record(rec: dict) -> dict:
    for key in REQUIRED:
        if key not in rec:
            raise ValueError(f"dataset record missing {key}")
    return {
        "id": rec["id"],
        "source_language": rec["source_language"],
        "target_language": rec["target_language"],
        "source": rec["source"],
        "reference": rec["reference"],
        "context": rec.get("context", ""),
        "genre": rec.get("genre", ""),
        "domain": rec.get("domain", ""),
        "entities": rec.get("entities", []),
        "terminology": rec.get("terminology", []),
        "metadata": rec.get("metadata", {}),
    }


class LocalFileAdapter:
    """JSONL file: one record per line."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> list[dict]:
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(normalize_record(json.loads(line)))
        return out


class HuggingFaceAdapter:
    """Load a parallel-text dataset from the Hub and normalize to records.

    Example: HuggingFaceAdapter("Helsinki-NLP/opus-zh-en", split="train[:100]")
    Expects a 'translation' column with {'zh': ..., 'en': ...} per row.
    """

    def __init__(self, name: str, split: str = "train[:10]", source_key: str = "zh", target_key: str = "en"):
        self.name = name
        self.split = split
        self.source_key = source_key
        self.target_key = target_key

    def load(self) -> list[dict]:
        from datasets import load_dataset

        ds = load_dataset(self.name, split=self.split)
        records = []
        for i, row in enumerate(ds):
            t = row.get("translation", row)
            src = t.get(self.source_key, "")
            ref = t.get(self.target_key, "")
            records.append(normalize_record({
                "id": f"{self.name}:{i}",
                "source_language": self.source_key,
                "target_language": self.target_key,
                "source": src,
                "reference": ref,
            }))
        return records


class OPUSAdapter(HuggingFaceAdapter):
    def __init__(self, dataset: str = "opus_books", source_key: str = "zh", target_key: str = "en", split: str = "train[:10]"):
        super().__init__(f"Helsinki-NLP/opus-{source_key}-{target_key}", split=split, source_key=source_key, target_key=target_key)


class ColumnAdapter:
    """Generic Hub adapter for datasets with named source/target columns.

    Example: ColumnAdapter("NilanE/ParallelFiction-Ja_En-100k", "train",
                           "src", "trg", "ja", "en")
    Downloads land in HF_HOME — point it at D:\\StoryTranslation_data\\hf so C: stays free.
    """

    def __init__(self, name: str, split: str = "train[:20]", source_col: str = "src", target_col: str = "trg",
                 source_lang: str = "ja", target_lang: str = "en", genre_col: str = "",
                 terminology_col: str = "", max_chars: int = 6000):
        self.name = name
        self.split = split
        self.source_col = source_col
        self.target_col = target_col
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.genre_col = genre_col
        self.terminology_col = terminology_col
        self.max_chars = max_chars

    def load(self) -> list[dict]:
        from datasets import load_dataset

        ds = load_dataset(self.name, split=self.split)
        records = []
        for i, row in enumerate(ds):
            src = row.get(self.source_col) or ""
            ref = row.get(self.target_col) or ""
            if not src.strip() or not ref.strip():
                continue
            terms: list = []
            raw_terms = row.get(self.terminology_col) if self.terminology_col else None
            if isinstance(raw_terms, list):
                for t in raw_terms:
                    if isinstance(t, dict) and t.get("term") and t.get("translation"):
                        terms.append({"term": t["term"], "translation": t["translation"]})
            genre = ""
            if self.genre_col:
                g = row.get(self.genre_col)
                genre = ", ".join(g) if isinstance(g, list) else str(g or "")
            records.append(normalize_record({
                "id": f"{self.name}:{i}",
                "source_language": self.source_lang,
                "target_language": self.target_lang,
                "source": src[:self.max_chars],
                "reference": ref[:self.max_chars],
                "genre": genre,
                "terminology": terms,
            }))
        return records


class ParallelFictionAdapter(ColumnAdapter):
    """NilanE/ParallelFiction-Ja_En-100k: chapter-level Japanese web novels with
    English fan translations (106k chapters, Apache-2.0 code / fair-use text).
    Closest public match to this project's ja->en chapter pipeline."""

    def __init__(self, split: str = "train[:20]", max_chars: int = 6000):
        super().__init__("NilanE/ParallelFiction-Ja_En-100k", split, "src", "trg", "ja", "en", max_chars=max_chars)


class OpenSakuraAdapter(ColumnAdapter):
    """OpenSakura LN ja->zh ALIGNED: chunk-level light-novel pairs WITH a gold
    glossary per row (term/translation). References are machine-generated
    (GLM-4.7), so chrF measures closeness to MT, not to humans — the terminology
    column is the valuable part (term-adherence scoring). License: other."""

    def __init__(self, split: str = "test[:20]", max_chars: int = 4000):
        super().__init__("OpenSakura/OpenSakura-DS-260220-LN-ja-zh-ALIGNED-Eve", split,
                         "source_text", "target_text", "ja", "zh",
                         terminology_col="glossary", max_chars=max_chars)


class WMTAdapter(HuggingFaceAdapter):
    def __init__(self, year: int = 19, source_key: str = "zh", target_key: str = "en", split: str = "train[:10]"):
        super().__init__(f"wmt{year}", split=split, source_key=source_key, target_key=target_key)


class FLORESAdapter(HuggingFaceAdapter):
    def __init__(self, split: str = "dev", source_key: str = "zho_Hans", target_key: str = "eng_Latn"):
        super().__init__("openlanguagedata/flores_plus", split=split, source_key=source_key, target_key=target_key)
