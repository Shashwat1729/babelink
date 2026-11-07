"""Hybrid context retrieval + budget assembly (spec §22, §23, §80).

Entity-aware retrieval: candidates are scored by present-entity sharing
(event continuity), recency, and token overlap. Segment scoring is
O(present_entities x candidates), never O(all_terms x candidates): the
terminology is scanned once to find entities literally present in the
query, then only those terms' alias lists are matched against segments.

Long-range links come from `all_segments` (full prior history); when it is
None the legacy `recent_segments` pool is used, so old callers keep working.
Summaries are also pulled from the Story Bible (chapter_summaries /
arc_summaries) via `load_section`.
"""
from __future__ import annotations

import json
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


def _load_terminology(project_dir) -> list[tuple[dict, list[str]]]:
    """(term_dict, forms) pairs from the Bible terminology section."""
    if not project_dir:
        return []
    try:
        terms = load_section(project_dir, "terminology")
    except Exception:
        return []
    out: list[tuple[dict, list[str]]] = []
    for t in terms if isinstance(terms, list) else []:
        if not isinstance(t, dict):
            continue
        forms = [f for f in (t.get("source_forms") or []) if isinstance(f, str) and f]
        if forms:
            out.append((t, forms))
    return out


def _load_bible_summaries(project_dir, chapter_summary: str = "") -> list[ContextItem]:
    """chapter_summaries (last 3) + arc_summaries (last 2) as summary items."""
    if not project_dir:
        return []
    items: list[ContextItem] = []
    seen = {chapter_summary} if chapter_summary else set()
    try:
        ch_sums = load_section(project_dir, "chapter_summaries")
    except Exception:
        ch_sums = []
    if isinstance(ch_sums, list):
        got = 0
        for entry in reversed(ch_sums):
            s = entry.get("summary", "") if isinstance(entry, dict) else entry if isinstance(entry, str) else ""
            if s and s not in seen:
                seen.add(s)
                items.append(ContextItem("summary", s, 2.0))
                got += 1
                if got >= 3:
                    break
    try:
        arc_sums = load_section(project_dir, "arc_summaries")
    except Exception:
        arc_sums = []
    if isinstance(arc_sums, list):
        got = 0
        for entry in reversed(arc_sums):
            s = entry.get("summary", "") if isinstance(entry, dict) else entry if isinstance(entry, str) else ""
            if s and s not in seen:
                seen.add(s)
                items.append(ContextItem("summary", s, 1.8))
                got += 1
                if got >= 2:
                    break
    return items


def gather_context(
    project_dir: str,
    source_text: str,
    recent_segments: list[str],
    chapter_summary: str = "",
    max_items: int = 8,
    *,
    all_segments: list[str] | None = None,
) -> list[ContextItem]:
    """Score bible terms, summaries, and prior segments for `source_text`.

    - bible_term: terminology terms with a form literally present in the
      query (boosted by present-form count).
    - previous_segment: every candidate segment, scored
      `3*entity_hits + 1.5*recency + 0.2*token_overlap`, where entity_hits
      counts present terms whose ANY alias appears in the segment (so an
      alias in the query links to a different alias in the segment with
      zero token overlap). Candidate pool is `all_segments` when given,
      else `recent_segments`.
    - summary: the passed `chapter_summary` plus Bible chapter/arc summaries.
    """
    items: list[ContextItem] = []
    terms = _load_terminology(project_dir)
    # (a) entities present: literal substring scan, once over terms.
    present: list[tuple[dict, list[str], list[str]]] = []
    for term, forms in terms:
        hits = [f for f in forms if f in source_text]
        if hits:
            present.append((term, forms, hits))
    for term, forms, hits in present:
        canon = term.get("canonical_translation", {})
        items.append(ContextItem("bible_term", f"{forms} -> {canon}", 4.0 + 0.5 * len(hits)))
    present_alias_lists = [forms for _, forms, _ in present]
    # (d) summaries: passed summary + Bible summaries.
    if chapter_summary:
        items.append(ContextItem("summary", chapter_summary, 2.5))
    items.extend(_load_bible_summaries(project_dir, chapter_summary))
    # (b)+(c) segments: entity continuity + recency + token overlap.
    candidates = list(all_segments) if all_segments is not None else list(recent_segments or [])
    n = len(candidates)
    src_tokens = _tokenize(source_text)
    for idx, seg in enumerate(candidates):
        distance = (n - 1 - idx) if n else 0  # 0 = most recent
        recency = 1.0 / (1 + distance)
        entity_hits = 0
        for forms in present_alias_lists:
            for f in forms:
                if f and f in seg:
                    entity_hits += 1
                    break
        overlap = len(src_tokens & _tokenize(seg)) if src_tokens and seg else 0
        score = 3.0 * entity_hits + 1.5 * recency + 0.2 * overlap
        items.append(ContextItem("previous_segment", seg, score))
    items.sort(key=lambda x: -x.score)
    return items[:max_items]


def render_context(items: list[ContextItem], max_chars: int = 4000) -> str:
    """Render items as `[kind] text` lines, never exceeding `max_chars`.

    The hard budget counts every rendered character: the `[kind] ` prefix
    and the `\\n` separators. Oversized items are skipped so a later small
    item can still fit; a single oversized first item is clipped to the
    budget. The result is always `<= max_chars`.
    """
    if max_chars <= 0:
        return ""
    parts: list[str] = []
    total = 0
    for it in items:
        line = f"[{it.kind}] {it.text}"
        sep = 1 if parts else 0
        cost = sep + len(line)
        if total + cost <= max_chars:
            parts.append(line)
            total += cost
        elif not parts:
            clipped = line[: max_chars - 1] + "…" if max_chars > 1 else line[:max_chars]
            parts.append(clipped)
            total = len(clipped)
            break
        # else: skip oversized item, a later smaller one may still fit.
    out = "\n".join(parts)
    if len(out) > max_chars:
        out = out[: max_chars - 1] + "…" if max_chars > 1 else out[:max_chars]
    return out


# ---- project-aware context for the pipeline (spec §22, §23, §80) ----------------------------------
_ENTITY_SECTIONS = ("characters", "locations", "organizations", "items", "abilities")
_NOTE_FIELDS = ("type", "gender", "pronouns", "notes", "relationship", "description")


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def build_context(db, project_id: int, seg, project_dir: str | None = None, *, n_prev: int = 3, n_summaries: int = 2,
                  max_chars: int = 2200, n_entity: int = 3) -> str:
    """What the model should know before translating `seg`, in priority order:
    1. user-written Bible notes for entities present (gender/pronouns decide he/she)
    2. the last few translated source=>translation pairs, crossing chapter boundaries
    3. summaries of the preceding chapters.
    Pasting a new chapter therefore continues from the old one automatically.

    Beyond the recent `n_prev`, up to `n_entity` LONG-RANGE translated
    segments that share a present entity (any alias) with `seg` are pulled
    in via SQL `instr` filtering (O(present x segments) in SQLite), so a
    chapter-3 callback to a chapter-1 event survives even with zero token
    overlap. When `n_prev`/`n_entity` are 0 (context feature off) no
    passages are added.
    """
    ch = db.conn.execute("SELECT * FROM chapters WHERE id=?", (seg["chapter_id"],)).fetchone()
    parts: list[str] = []

    present_alias_lists: list[list[str]] = []
    if project_dir:
        notes = []
        for section in _ENTITY_SECTIONS:
            for e in load_section(project_dir, section):
                if isinstance(e, dict) and e.get("source") and e["source"] in seg["source_text"]:
                    present_alias_lists.append([e["source"]])
                    extra = ", ".join(f"{k}: {e[k]}" for k in _NOTE_FIELDS if e.get(k))
                    if extra:
                        notes.append(f"{e['source']} = {e.get('translation', '')} ({extra})")
        if notes:
            parts.append("[Known entities]\n" + "\n".join(notes))
    # DB terminology rows: present terms define the long-range entity query.
    try:
        for t in db.terminology_for_project(project_id):
            forms = [f for f in (json.loads(t["source_forms"]) or []) if isinstance(f, str) and f]
            if forms and any(f in (seg["source_text"] or "") for f in forms):
                present_alias_lists.append(forms)
    except Exception:
        pass

    prev: list = []
    if n_prev > 0 or n_entity > 0:
        if ch is None:
            prev = []
        else:
            recent = list(db.previous_translated(project_id, ch["order_index"], seg["order_index"], n=n_prev)) if n_prev > 0 else []
            linked: list = []
            if n_entity > 0:
                flat: list[str] = []
                seen_flat: set[str] = set()
                for forms in present_alias_lists:
                    for f in forms:
                        if f not in seen_flat:
                            seen_flat.add(f)
                            flat.append(f)
                flat = flat[:30]  # cap SQL width; present entities are few
                if flat:
                    cond = " OR ".join(["instr(s.source_text, ?) > 0"] * len(flat))
                    # Fetch a few extra so dedup against `recent` still leaves n_entity new rows.
                    rows = db.conn.execute(
                        "SELECT s.* FROM segments s JOIN chapters c ON c.id=s.chapter_id "
                        "WHERE s.project_id=? AND s.translated_text IS NOT NULL "
                        "AND (c.order_index < ? OR (c.order_index=? AND s.order_index < ?)) "
                        f"AND ({cond}) ORDER BY c.order_index DESC, s.order_index DESC LIMIT ?",
                        (project_id, ch["order_index"], ch["order_index"], seg["order_index"],
                         *flat, n_entity + n_prev),
                    ).fetchall()
                    recent_ids = {r["id"] for r in recent}
                    for r in reversed(rows):  # reading order
                        if r["id"] not in recent_ids:
                            linked.append(r)
                            if len(linked) >= n_entity:
                                break
            prev = recent + linked
            # Reading order: chapters/order_index, not score order, preserves narrative flow.
            ch_order = {r["id"]: r["order_index"] for r in db.conn.execute(
                "SELECT id, order_index FROM chapters WHERE project_id=?", (project_id,)).fetchall()}
            prev.sort(key=lambda r: (ch_order.get(r["chapter_id"], 0), r["order_index"]))
    if prev:
        parts.append("[Previous passages, source => translation]\n"
                     + "\n".join(f"{_clip(p['source_text'], 300)} => {_clip(p['translated_text'], 600)}" for p in prev))

    earlier = db.conn.execute(
        "SELECT order_index, title, summary FROM chapters WHERE project_id=? AND order_index<? AND summary IS NOT NULL AND summary<>'' "
        "ORDER BY order_index DESC LIMIT ?", (project_id, ch["order_index"] if ch else 0, n_summaries)).fetchall() if n_summaries > 0 else []
    if earlier:
        parts.append("[Story so far]\n" + "\n".join(f"{_clip(r['title'], 40)}: {_clip(r['summary'], 400)}" for r in reversed(earlier)))
    if project_dir and n_summaries > 0:
        try:
            arcs = load_section(project_dir, "arc_summaries")
        except Exception:
            arcs = []
        if isinstance(arcs, list) and arcs:
            last = arcs[-1]
            s = last.get("summary", "") if isinstance(last, dict) else last if isinstance(last, str) else ""
            if s:
                parts.append(f"[Arc summary]\n{_clip(s, 400)}")

    while len(parts) > 1 and sum(map(len, parts)) > max_chars:
        parts.pop()  # lowest priority (summaries) goes first
    return _clip("\n\n".join(parts), max_chars)
