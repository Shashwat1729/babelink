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


class WMTAdapter(HuggingFaceAdapter):
    def __init__(self, year: int = 19, source_key: str = "zh", target_key: str = "en", split: str = "train[:10]"):
        super().__init__(f"wmt{year}", split=split, source_key=source_key, target_key=target_key)


class FLORESAdapter(HuggingFaceAdapter):
    def __init__(self, split: str = "dev", source_key: str = "zho_Hans", target_key: str = "eng_Latn"):
        super().__init__("openlanguagedata/flores_plus", split=split, source_key=source_key, target_key=target_key)
