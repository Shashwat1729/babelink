"""Entity graph: typed story entities + typed edges (narrative knowledge graph).

Replaces the flat Story Bible sections (lists of dicts with no typed nodes and no
edges) with a real graph layer, while keeping the on-disk section JSON backward
compatible: entity sections stay lists of dicts, enriched with stable ids, and
the `relationships` section holds the edges.

Design (borrowed ideas, implemented from scratch):
- Knowledge-graph core: entities are typed nodes (character/place/org/item/
  ability/faction) with stable, human-readable ids (`character:林凡`); edges are
  typed (member_of, allied_with, enemy_of, located_in, owns, uses,
  appears_in_chapter, active_in_chapter, co_occurs).
- Dictionary entity linking: an entity is "active" in a segment when any of its
  name forms (aliases included) appears as a substring of the segment text.
  Deterministic and language-agnostic (no tokenization, works for CJK).
- Co-occurrence discovery: entities that are active in the same chapters are
  proposed as `co_occurs` recommendations for human review, scored by the
  number of shared chapters — never auto-applied.
- Chapter presence: `chapter_entity_map` scans DB terminology + segments once,
  in reading order, so `first_seen` (chapter order_index of first observation)
  and appears/active chapter edges are deterministic.

Unknown fields are always preserved (entities and edges are open dicts); nothing
is dropped on load/save. No DB schema changes: the graph derives from the
existing `terminology` table and `segments`, and persists through the Story
Bible JSON sections.
"""
from __future__ import annotations

import json
import re
import sqlite3
from itertools import combinations
from pathlib import Path
from typing import Any

ENTITY_TYPES = ("character", "place", "org", "item", "ability", "faction")
EDGE_TYPES = (
    "member_of",
    "allied_with",
    "enemy_of",
    "located_in",
    "owns",
    "uses",
    "appears_in_chapter",
    "active_in_chapter",
    "co_occurs",
)
CHAPTER_EDGE_TYPES = ("appears_in_chapter", "active_in_chapter")
CHAPTER_PREFIX = "chapter:"

SECTION_TO_TYPE = {
    "characters": "character",
    "locations": "place",
    "organizations": "org",
    "items": "item",
    "abilities": "ability",
    "factions": "faction",
}
TYPE_TO_SECTION = {v: k for k, v in SECTION_TO_TYPE.items()}
ENTITY_SECTIONS = tuple(SECTION_TO_TYPE)

# terminology `concept` values (from the term helper) -> entity type. Mirrors
# terminology._BIBLE_SECTION (technique/realm -> abilities).
CONCEPT_TO_TYPE = {
    "character": "character",
    "person": "character",
    "name": "character",
    "location": "place",
    "place": "place",
    "organization": "org",
    "org": "org",
    "item": "item",
    "technique": "ability",
    "realm": "ability",
    "ability": "ability",
    "faction": "faction",
}


def chapter_target(chapter_id: int) -> str:
    return f"{CHAPTER_PREFIX}{chapter_id}"


def slug_id(entity_type: str, name: str, existing: dict | None = None) -> str:
    """Stable `type:name` id; suffix `#2`, `#3`, … only on genuine collision
    (same name under a different type is already distinct by the prefix)."""
    base = f"{entity_type}:{name}"
    existing = existing or {}
    if base not in existing or existing[base].get("name", name) == name:
        return existing.get(base, {}).get("id", base) if base in existing else base
    n = 2
    while f"{base}#{n}" in existing:
        n += 1
    return f"{base}#{n}"


def _norm_names(names: Any, primary: str | None = None) -> list[str]:
    out: list[str] = []
    cand: list[Any] = [primary] if primary else []
    if isinstance(names, str):
        cand.append(names)
    elif isinstance(names, (list, tuple)):
        cand.extend(names)
    for n in cand:
        if isinstance(n, str) and n and n not in out:
            out.append(n)
    return out


class EntityGraph:
    """In-memory entity graph. Entities and edges are plain dicts (open
    schemas), so any extra field a caller adds survives to_dict/save/load."""

    def __init__(self, entities: list[dict] | None = None, edges: list[dict] | None = None):
        self._entities: dict[str, dict] = {}
        self._edges: list[dict] = []
        for e in entities or []:
            self._ingest_entity(e)
        for edge in edges or []:
            self._edges.append(self._normalize_edge(edge))

    # ---- entity CRUD -------------------------------------------------------
    def add_entity(
        self,
        entity_type: str,
        name: str | None = None,
        names: Any = None,
        *,
        id: str | None = None,
        importance: int | float = 1,
        first_seen: int | None = None,
        **attrs: Any,
    ) -> str:
        """Upsert by stable id: re-adding a known `type:name` merges name forms
        and updates attributes instead of duplicating the entity."""
        names_list = _norm_names(names, name)
        if not names_list:
            raise ValueError("add_entity needs a name or names")
        primary = names_list[0]
        entity_type = entity_type or "character"
        eid = id or slug_id(entity_type, primary, self._entities)
        ent = self._entities.get(eid)
        if ent is None:
            ent = {"id": eid, "type": entity_type, "names": names_list, "name": primary}
            if isinstance(importance, (int, float)):
                ent["importance"] = importance
            if first_seen is not None:
                ent["first_seen"] = first_seen
            self._entities[eid] = ent
        else:
            for n in names_list:
                if n not in ent["names"]:
                    ent["names"].append(n)
        if isinstance(importance, (int, float)) and ("importance" not in ent or importance != 1):
            ent["importance"] = max(ent.get("importance", 1), importance) if ent.get("importance") else importance
        if first_seen is not None and ent.get("first_seen") is None:
            ent["first_seen"] = first_seen
        ent.update({k: v for k, v in attrs.items() if k not in ent})
        for k, v in attrs.items():
            ent[k] = v  # explicit attrs win on update; unknown fields preserved
        ent["id"], ent["type"], ent["names"], ent["name"] = eid, entity_type, ent["names"], ent["names"][0]
        return eid

    def _ingest_entity(self, e: dict) -> None:
        names = _norm_names(e.get("names"), e.get("name") or e.get("source"))
        if not names:
            names = [e.get("id") or "entity"]
        eid = e.get("id") or slug_id(e.get("type", "character"), names[0], self._entities)
        if eid in self._entities:
            self.update_entity(eid, **{k: v for k, v in e.items() if k != "id"})
            return
        ent = {**e, "id": eid, "type": e.get("type", "character"), "names": names, "name": names[0]}
        self._entities[eid] = ent

    def get(self, entity_id: str) -> dict | None:
        ent = self._entities.get(entity_id)
        return dict(ent) if ent else None

    def entities(self, entity_type: str | None = None) -> list[dict]:
        out = [dict(e) for e in self._entities.values() if entity_type is None or e.get("type") == entity_type]
        out.sort(key=lambda e: (e.get("type", ""), e.get("name", ""), e["id"]))
        return out

    def update_entity(self, entity_id: str, **fields: Any) -> dict:
        if entity_id not in self._entities:
            raise KeyError(f"unknown entity {entity_id!r}")
        ent = self._entities[entity_id]
        cur = ent.get("first_seen")
        for k, v in fields.items():
            if k == "id":
                continue
            if k == "names":
                for n in _norm_names(v):
                    if n not in ent["names"]:
                        ent["names"].append(n)
            elif k == "name" and v:
                if v not in ent["names"]:
                    ent["names"].insert(0, v)
                ent["name"] = v
            elif k == "first_seen" and isinstance(v, int) and isinstance(cur, int):
                ent["first_seen"] = min(cur, v)
            else:
                ent[k] = v
        ent["name"] = ent["names"][0] if ent.get("names") else ent.get("name", "")
        return dict(ent)

    def remove_entity(self, entity_id: str) -> bool:
        if entity_id not in self._entities:
            return False
        del self._entities[entity_id]
        self._edges = [e for e in self._edges if e["source"] != entity_id and e["target"] != entity_id]
        return True

    def resolve(self, name_or_id: str) -> str | None:
        """Find an entity by id, primary name, or any alias form."""
        if name_or_id in self._entities:
            return name_or_id
        for e in self._entities.values():
            if name_or_id in e.get("names", []):
                return e["id"]
        return None

    # ---- edges -------------------------------------------------------------
    @staticmethod
    def _normalize_edge(e: dict) -> dict:
        edge = dict(e)
        edge.setdefault("type", "co_occurs")
        edge.setdefault("count", 1)
        if edge.get("chapter_id") is not None and not edge.get("chapters"):
            edge["chapters"] = [edge["chapter_id"]]
        edge["chapters"] = sorted(set(edge.get("chapters") or []))
        return edge

    def link(self, source: str, target: str, edge_type: str, *, count: int = 1, chapters: Any = None,
             chapter_id: int | None = None, **attrs: Any) -> dict:
        if edge_type not in EDGE_TYPES:
            raise ValueError(f"unknown edge type {edge_type!r}; expected one of {', '.join(EDGE_TYPES)}")
        if source == target:
            raise ValueError("self-links are not allowed")
        for ep in (source, target):
            if ep.startswith(CHAPTER_PREFIX):
                continue
            if ep not in self._entities:
                raise KeyError(f"unknown entity {ep!r}")
        edge = self._normalize_edge({"source": source, "target": target, "type": edge_type, "count": count,
                                     "chapters": chapters if chapters is not None else ([chapter_id] if chapter_id is not None else [])})
        edge.update(attrs)
        existing = next((e for e in self._edges
                         if (e["source"], e["target"], e["type"]) == (source, target, edge_type)), None)
        if existing is None:
            self._edges.append(edge)
            return dict(edge)
        if edge_type not in CHAPTER_EDGE_TYPES:  # chapter presence is idempotent, never inflates
            existing["count"] = int(existing.get("count", 1)) + int(edge.get("count", 1))
        existing["chapters"] = sorted(set(existing.get("chapters") or []) | set(edge.get("chapters") or []))
        for k, v in attrs.items():
            existing.setdefault(k, v)
        return dict(existing)

    def unlink(self, source: str, target: str, edge_type: str) -> bool:
        before = len(self._edges)
        self._edges = [e for e in self._edges if (e["source"], e["target"], e["type"]) != (source, target, edge_type)]
        return len(self._edges) != before

    def relations_of(self, entity_id: str) -> list[dict]:
        """All edges touching the entity, with direction and the resolved
        counterpart (`other_id`; chapter targets stay as `chapter:<id>`)."""
        if entity_id not in self._entities:
            raise KeyError(f"unknown entity {entity_id!r}")
        out = []
        for e in self._edges:
            if e["source"] == entity_id:
                out.append({"type": e["type"], "direction": "out", "source": e["source"], "target": e["target"],
                            "other_id": e["target"], "count": e.get("count", 1), "chapters": e.get("chapters", [])})
            elif e["target"] == entity_id:
                out.append({"type": e["type"], "direction": "in", "source": e["source"], "target": e["target"],
                            "other_id": e["source"], "count": e.get("count", 1), "chapters": e.get("chapters", [])})
        out.sort(key=lambda r: (r["type"], r["other_id"]))
        return out

    # ---- entity linking ----------------------------------------------------
    def entities_active_in(self, segment: Any) -> list[dict]:
        """Entities whose any name form occurs in the segment text (substring
        dictionary match; sorted by importance desc, then name)."""
        if isinstance(segment, str):
            text = segment
        elif isinstance(segment, dict):
            text = str(segment.get("source_text") or "")
        elif isinstance(segment, (list, tuple)):
            text = " ".join(str(s) for s in segment)
        elif isinstance(segment, sqlite3.Row):
            text = str(segment["source_text"] or "")
        else:
            text = str(segment)
        if not text:
            return []
        hits = [dict(e) for e in self._entities.values() if any(f and f in text for f in e.get("names", []))]
        hits.sort(key=lambda e: (-(e.get("importance") if isinstance(e.get("importance"), (int, float)) else 1), e.get("name", ""), e["id"]))
        return hits

    # ---- serialization -----------------------------------------------------
    def to_dict(self) -> dict:
        return {"entities": self.entities(), "edges": [dict(e) for e in self._edges]}

    @classmethod
    def from_dict(cls, data: dict) -> "EntityGraph":
        return cls(entities=data.get("entities", []), edges=data.get("edges", []))

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "EntityGraph":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_sections(self) -> dict[str, list[dict]]:
        """Group entities back into Bible sections; edges become `relationships`."""
        sections: dict[str, list[dict]] = {s: [] for s in SECTION_TO_TYPE}
        for e in self.entities():
            section = TYPE_TO_SECTION.get(e.get("type", ""), "characters")
            entry = dict(e)
            entry.setdefault("source", entry["name"])
            sections[section].append(entry)
        sections["relationships"] = [dict(e) for e in self._edges]
        return sections


# ---- Bible <-> graph adapters (used by story_bible; no import cycle) ---------
def _entry_id(entry: dict, entity_type: str, used: dict) -> str:
    if entry.get("id"):
        return entry["id"]
    names = _norm_names(entry.get("names"),
                        entry.get("name") or entry.get("source")
                        or (entry.get("source_forms") or [None])[0])
    if not names:
        names = [f"entity{len(used) + 1}"]
    eid = slug_id(entity_type, names[0], used)
    used[eid] = entry
    return eid


def normalize_bible_entries(section: str, data: Any) -> Any:
    """Validate/normalize Bible section data on load AND save. Normalizing is
    enrichment only (id/type/names/importance), never a rewrite: every original
    field survives, so old files and callers (context/web/sync) keep working."""
    if not isinstance(data, list):
        return data
    if section in SECTION_TO_TYPE:
        etype = SECTION_TO_TYPE[section]
        used: dict[str, dict] = {}
        out: list[Any] = []
        for e in data:
            if not isinstance(e, dict):
                out.append(e)
                continue
            names = _norm_names(e.get("names"), e.get("name") or e.get("source"))
            ent = dict(e)
            ent["id"] = _entry_id(ent, etype, used)
            used[ent["id"]] = ent
            ent["type"] = e.get("type") or etype
            ent["names"] = names or [ent["id"]]
            ent["name"] = ent["names"][0]
            ent.setdefault("source", ent["name"])
            if "importance" not in ent:
                occ = e.get("occurrences")
                ent["importance"] = occ if isinstance(occ, (int, float)) and occ else 1
            out.append(ent)
        return out
    if section == "terminology":
        used = {}
        out = []
        for e in data:
            if not isinstance(e, dict):
                out.append(e)
                continue
            forms = e.get("source_forms") or ([e["source"]] if e.get("source") else [])
            ent = dict(e)
            concept = str(e.get("concept") or "")
            etype = CONCEPT_TO_TYPE.get(concept.lower(), "character")
            ent["id"] = _entry_id(ent, etype, used)
            used[ent["id"]] = ent
            ent["type"] = e.get("type") or etype
            ent["names"] = _norm_names(forms) or [ent["id"]]
            ent.setdefault("importance", e["occurrences"] if isinstance(e.get("occurrences"), (int, float)) and e["occurrences"] else 1)
            out.append(ent)
        return out
    if section == "relationships":
        # Edges may name endpoints by id or by display name; keep both, never drop.
        return [dict(e) if isinstance(e, dict) else e for e in data]
    return data


def graph_from_bible(project_dir: str | Path) -> EntityGraph:
    """Rebuild the graph from the on-disk Bible sections (entities + edges)."""
    base = Path(project_dir) / "story_bible"

    def raw(section: str) -> list:
        f = base / f"{section}.json"
        if not f.exists():
            return []
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []

    g = EntityGraph()
    for section, etype in SECTION_TO_TYPE.items():
        for e in normalize_bible_entries(section, raw(section)):
            if isinstance(e, dict):
                g._ingest_entity(e)
    for e in normalize_bible_entries("terminology", raw("terminology")):
        if isinstance(e, dict) and e.get("source_forms"):
            e = dict(e)
            e["source"] = (e.get("source_forms") or [""])[0]
            g._ingest_entity(e)
    for e in raw("relationships"):
        if not isinstance(e, dict):
            continue
        edge = dict(e)
        for key in ("source", "target"):
            if edge.get(key) and not edge[key].startswith(CHAPTER_PREFIX):
                edge[key] = g.resolve(edge[key]) or edge[key]
        g._edges.append(g._normalize_edge(edge))
    return g


def graph_to_bible(graph: EntityGraph, project_dir: str | Path) -> None:
    """Persist the graph through the existing section files (entities grouped
    by type, edges in `relationships`). Non-entity sections are untouched."""
    base = Path(project_dir) / "story_bible"
    base.mkdir(parents=True, exist_ok=True)
    for section, entries in graph.to_sections().items():
        (base / f"{section}.json").write_text(
            json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


# ---- DB-derived graph -------------------------------------------------------
def graph_from_db(db, project_id: int) -> EntityGraph:
    """Build the graph from DB terminology (+ segment scan for first_seen).
    Deterministic: rows in terminology order, first_seen = first chapter
    order_index whose segments mention the entity."""
    g = EntityGraph()
    target = db.get_project(project_id)["target_lang"]
    for t in db.terminology_for_project(project_id):
        if t["status"] == "ignored":
            continue
        forms = json.loads(t["source_forms"])
        if not forms:
            continue
        canon = json.loads(t["canonical_translation"] or "{}")
        concept = t["concept"] or ""
        etype = CONCEPT_TO_TYPE.get(concept.lower(), "character")
        occ = t["occurrences"] or 0
        eid = g.add_entity(etype, names=forms, importance=occ if occ else 1,
                           concept=concept, occurrences=occ, status=t["status"], policy=t["policy"])
        g.update_entity(eid, translation=canon.get(target) or "")
    for ch in db.chapters(project_id):
        for s in db.segments_for_chapter(ch["id"]):
            for hit in g.entities_active_in(s["source_text"] or ""):
                if hit.get("first_seen") is None:
                    g.update_entity(hit["id"], first_seen=ch["order_index"])
    return g


def chapter_entity_map(db, project_id: int, graph: EntityGraph | None = None) -> dict[int, list[str]]:
    """{chapter_id: [entity_id, …]} built from DB terminology + segments.

    Also records (idempotently) appears_in_chapter / active_in_chapter edges on
    `graph` (built from the DB when not supplied): appears = seen in the chapter
    at all; active = seen in 2+ segments of the chapter. Sets `first_seen`."""
    g = graph or graph_from_db(db, project_id)
    result: dict[int, list[str]] = {}
    for ch in db.chapters(project_id):
        per_entity_segments: dict[str, int] = {}
        for s in db.segments_for_chapter(ch["id"]):
            for hit in g.entities_active_in(s["source_text"] or ""):
                per_entity_segments[hit["id"]] = per_entity_segments.get(hit["id"], 0) + 1
                if g.get(hit["id"]).get("first_seen") is None:
                    g.update_entity(hit["id"], first_seen=ch["order_index"])
        ids = sorted(per_entity_segments)
        result[ch["id"]] = ids
        for eid, n in per_entity_segments.items():
            g.link(eid, chapter_target(ch["id"]), "appears_in_chapter", chapters=[ch["id"]])
            if n >= 2:
                g.link(eid, chapter_target(ch["id"]), "active_in_chapter", chapters=[ch["id"]])
    return result


def link_recommendations(graph: EntityGraph, chapter_map: dict[int, list[str]] | None = None,
                         min_count: int = 2) -> list[dict]:
    """Co-occurrence suggestions for human review: entity pairs active in the
    same `count` chapters (default >= 2). Pairs already carrying an explicit
    relation are skipped. Sorted by count desc; ties by id. Never mutates."""
    cmap = chapter_map
    if cmap is None:
        cmap = {}
        for e in graph._edges:
            if e["type"] in CHAPTER_EDGE_TYPES and e["target"].startswith(CHAPTER_PREFIX):
                cid = int(e["target"].split(":", 1)[1])
                cmap.setdefault(cid, set()).add(e["source"])
    explicit = {(e["source"], e["target"]) for e in graph._edges
                if e["type"] not in CHAPTER_EDGE_TYPES + ("co_occurs",)} | \
               {(e["target"], e["source"]) for e in graph._edges
                if e["type"] not in CHAPTER_EDGE_TYPES + ("co_occurs",)}
    counts: dict[tuple[str, str], list[int]] = {}
    for cid, ids in cmap.items():
        id_list = sorted({i for i in (ids if isinstance(ids, (list, tuple, set)) else [ids])
                          if not str(i).startswith(CHAPTER_PREFIX)})
        for a, b in combinations(id_list, 2):
            counts.setdefault((a, b), []).append(cid)
    recs = [{"source": a, "target": b, "type": "co_occurs", "count": len(chs), "chapters": sorted(chs)}
            for (a, b), chs in counts.items() if len(chs) >= min_count and (a, b) not in explicit]
    recs.sort(key=lambda r: (-r["count"], r["source"], r["target"]))
    return recs


def apply_recommendation(graph: EntityGraph, rec: dict) -> dict:
    """Materialize an approved recommendation as a `co_occurs` edge."""
    return graph.link(rec["source"], rec["target"], "co_occurs",
                      count=int(rec.get("count", 1)), chapters=rec.get("chapters"))
