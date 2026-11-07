"""Story Bible: user-editable JSON knowledge base per project (spec §11).

Sections are migrated onto the entity graph (`babelink.graph`): entity
sections (characters, locations, organizations, items, abilities, factions)
and terminology are normalized on load/save (stable ids, types, name forms,
importance) while preserving every original field, so old files and callers
(pipeline/context/summaries/web) keep working unchanged. Edges live in the
`relationships` section. Use `load_graph` / `save_graph` / `link_entities` for
the graph view.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import graph as _graph

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


def _raw(project_dir: str | Path, section: str) -> Any:
    f = Path(project_dir) / "story_bible" / f"{section}.json"
    if not f.exists():
        return [] if section != "style_guide" else {}
    return json.loads(f.read_text(encoding="utf-8"))


def load_section(project_dir: str | Path, section: str) -> Any:
    data = _raw(project_dir, section)
    return _graph.normalize_bible_entries(section, data)


def save_section(project_dir: str | Path, section: str, data: Any) -> None:
    f = bible_dir(project_dir) / f"{section}.json"
    data = _graph.normalize_bible_entries(section, data)
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


# ---- graph API ---------------------------------------------------------------
def load_graph(project_dir: str | Path) -> _graph.EntityGraph:
    """Rebuild the entity graph from the section files on disk."""
    return _graph.graph_from_bible(project_dir)


def save_graph(project_dir: str | Path, graph: _graph.EntityGraph) -> None:
    """Persist the graph through the section files (entities + relationships)."""
    _graph.graph_to_bible(graph, project_dir)


def link_entities(project_dir: str | Path, source: str, target: str, edge_type: str, **attrs: Any) -> dict:
    """Add one typed edge and persist it via the `relationships` section."""
    g = load_graph(project_dir)
    edge = g.link(source, target, edge_type, **attrs)
    save_graph(project_dir, g)
    return edge


def relations_of(project_dir: str | Path, entity_id: str) -> list[dict]:
    return load_graph(project_dir).relations_of(entity_id)


def entities_active_in(project_dir: str | Path, segment: Any) -> list[dict]:
    return load_graph(project_dir).entities_active_in(segment)
