"""Story Bible: user-editable JSON knowledge base per project (spec §11)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SECTIONS = [
    "characters",
    "locations",
    "organizations",
    "terminology",
    "items",
    "abilities",
    "relationships",
    "timeline",
    "factions",
    "world_rules",
    "style_guide",
    "chapter_summaries",
    "arc_summaries",
    "contradictions",
]


def bible_dir(project_dir: str | Path) -> Path:
    d = Path(project_dir) / "story_bible"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_section(project_dir: str | Path, section: str) -> Any:
    f = bible_dir(project_dir) / f"{section}.json"
    if not f.exists():
        return [] if section != "style_guide" else {}
    return json.loads(f.read_text(encoding="utf-8"))


def save_section(project_dir: str | Path, section: str, data: Any) -> None:
    f = bible_dir(project_dir) / f"{section}.json"
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def add_conflict(project_dir: str | Path, conflict: dict) -> None:
    items = load_section(project_dir, "contradictions")
    items.append(conflict)
    save_section(project_dir, "contradictions", items)


def project_root(workspace: str | Path, project_id: int) -> str:
    """Where a project's Story Bible lives inside a workspace (one workspace DB can hold many projects)."""
    d = Path(workspace) / "projects" / str(project_id)
    d.mkdir(parents=True, exist_ok=True)
    return str(d)
