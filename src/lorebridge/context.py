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


# ---- project-aware context for the pipeline (spec §22, §23, §80) ----------------------------------
_ENTITY_SECTIONS = ("characters", "locations", "organizations", "items", "abilities")
_NOTE_FIELDS = ("type", "gender", "pronouns", "notes", "relationship", "description")


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def build_context(db, project_id: int, seg, project_dir: str | None = None, *, n_prev: int = 3, n_summaries: int = 2,
                  max_chars: int = 2200) -> str:
    """What the model should know before translating `seg`, in priority order:
    1. user-written Bible notes for entities present (gender/pronouns decide he/she)
    2. the last few translated source=>translation pairs, crossing chapter boundaries
    3. summaries of the preceding chapters.
    Pasting a new chapter therefore continues from the old one automatically."""
    ch = db.conn.execute("SELECT * FROM chapters WHERE id=?", (seg["chapter_id"],)).fetchone()
    parts: list[str] = []

    if project_dir:
        notes = []
        for section in _ENTITY_SECTIONS:
            for e in load_section(project_dir, section):
                if isinstance(e, dict) and e.get("source") and e["source"] in seg["source_text"]:
                    extra = ", ".join(f"{k}: {e[k]}" for k in _NOTE_FIELDS if e.get(k))
                    if extra:
                        notes.append(f"{e['source']} = {e.get('translation', '')} ({extra})")
        if notes:
            parts.append("[Known entities]\n" + "\n".join(notes))

    prev = db.previous_translated(project_id, ch["order_index"], seg["order_index"], n=n_prev)
    if prev:
        parts.append("[Previous passages, source => translation]\n"
                     + "\n".join(f"{_clip(p['source_text'], 300)} => {_clip(p['translated_text'], 600)}" for p in prev))

    earlier = db.conn.execute(
        "SELECT order_index, title, summary FROM chapters WHERE project_id=? AND order_index<? AND summary IS NOT NULL AND summary<>'' "
        "ORDER BY order_index DESC LIMIT ?", (project_id, ch["order_index"], n_summaries)).fetchall()
    if earlier:
        parts.append("[Story so far]\n" + "\n".join(f"{_clip(r['title'], 40)}: {_clip(r['summary'], 400)}" for r in reversed(earlier)))

    while len(parts) > 1 and sum(map(len, parts)) > max_chars:
        parts.pop()  # lowest priority (summaries) goes first
    return _clip("\n\n".join(parts), max_chars)
