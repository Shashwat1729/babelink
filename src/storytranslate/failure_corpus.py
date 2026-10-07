"""MTL Failure Corpus (spec §53): records of bad MTL + reference + error tags."""
from __future__ import annotations

import json
from pathlib import Path

REQUIRED = ["source", "bad_mtl", "better", "errors"]


def add_case(corpus_path: str | Path, case: dict) -> None:
    for key in REQUIRED:
        if key not in case:
            raise ValueError(f"failure case missing {key}")
    p = Path(corpus_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(case, ensure_ascii=False) + "\n")


def load_cases(corpus_path: str | Path) -> list[dict]:
    p = Path(corpus_path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
