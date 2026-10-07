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
