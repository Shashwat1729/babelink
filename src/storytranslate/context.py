"""Hybrid context retrieval + budget assembly (spec §22, §23, §80)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .story_bible import load_section


@dataclass
class ContextItem:
    kind: str  # bible_term | previous_segment | summary
    text: str
    score: float


def _tokenize(s: str) -> set[str]:
    return set(re.findall(r"\w+", s.lower()))


def gather_context(
    project_dir: str,
    source_text: str,
    recent_segments: list[str],
    chapter_summary: str = "",
    max_items: int = 8,
) -> list[ContextItem]:
    items: list[ContextItem] = []
    terms = load_section(project_dir, "terminology")
    src_tokens = _tokenize(source_text)
    for t in (terms if isinstance(terms, list) else []):
        forms = t.get("source_forms", [])
        canon = t.get("canonical_translation", {})
        overlap = len(src_tokens & _tokenize(" ".join(forms)))
        present = sum(1 for f in forms if f in source_text)
        score = 2.0 * present + 0.5 * overlap
        if score > 0:
            items.append(ContextItem("bible_term", f"{forms} -> {canon}", score))
    if chapter_summary:
        items.append(ContextItem("summary", chapter_summary, 1.0))
    # recency-weighted recent segments
    for i, seg in enumerate(reversed(recent_segments)):
        recency = 1.0 / (1 + i)
        overlap = len(src_tokens & _tokenize(seg))
        score = recency + 0.3 * overlap
        items.append(ContextItem("previous_segment", seg, score))
    items.sort(key=lambda x: -x.score)
    return items[:max_items]


def render_context(items: list[ContextItem], max_chars: int = 4000) -> str:
    parts: list[str] = []
    budget = 0
    for it in items:
        if budget + len(it.text) > max_chars:
            break
        parts.append(f"[{it.kind}] {it.text}")
        budget += len(it.text)
    return "\n".join(parts)
