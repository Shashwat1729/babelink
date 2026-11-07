"""Entity-first translation: mask glossary names as opaque proxies before the MT
call, restore renderings after. A hinted name can be ignored or misspelled by
the model; a proxy cannot — consistency becomes structural (100% by
construction) instead of probabilistic. See EXPERIMENTS E09.

Proxy style matters (the MT model must pass it through untouched): `style`
selects the surface (`bracket` ⟦E3⟧, `at` @@E3@@, `under` __E3__). Which one a
model passes through is measured live (E09 probe), not assumed here.
"""
from __future__ import annotations

import json
import re

STYLES = ("bracket", "at", "under")


def proxy(eid: int, style: str = "bracket") -> str:
    if style == "at":
        return f"@@E{eid}@@"
    if style == "under":
        return f"__E{eid}__"
    return f"\u27e6E{eid}\u27e7"  # ⟦E3⟧


_PROXY_RE = re.compile(r"(?:\u27e6E(\d+)\u27e7|@@E(\d+)@@|__E(\d+)__|\[E(\d+)\]|\?E(\d+)\?)")
# NOTE: [E4] and ?E4? are MODEL rewrites, not emitted styles — Hy-MT2 sometimes
# normalizes ⟦E4⟧ in flight (Red Chamber DG16/DG17). Restoring them is safe:
# neither occurs in real prose. Bare E4 is NOT matched (collides with real
# text like 'vitamin E4').


def mask_entities(text: str, entities: list[tuple[str, int]], style: str = "bracket") -> tuple[str, dict[int, str]]:
    """Replace every occurrence of each entity form (longest first, single
    pass) with its proxy. Returns (masked, {eid: proxy}). Forms already
    containing ASCII proxy text are never double-masked: CJK forms can't match
    ASCII proxies, and proxies are inserted longest-first so nested forms
    (林黛玉 over 黛玉) mask once."""
    masked = text
    used: dict[int, str] = {}
    for form, eid in sorted(entities, key=lambda e: len(e[0]), reverse=True):
        if form and form in masked:
            masked = masked.replace(form, proxy(eid, style))
            used[eid] = proxy(eid, style)
    return masked, used


def restore_proxies(text: str, renderings: dict[int, str]) -> tuple[str, list[int]]:
    """Substitute proxies with final renderings (longest proxy first so E12
    never collides with E1). Returns (restored, [eids still unreplaced])."""

    def eid_of(m: re.Match) -> int:
        return int(next(g for g in m.groups() if g is not None))

    missing = sorted({eid_of(m) for m in _PROXY_RE.finditer(text) if eid_of(m) not in renderings})

    def sub(m: re.Match) -> str:
        return renderings.get(eid_of(m), m.group(0))

    return _PROXY_RE.sub(sub, text), missing


def entities_for_segment(glossary: dict[str, str], source: str, db=None, project_id: int = 0) -> tuple[list[tuple[str, int]], dict[int, str]]:
    """Glossary forms present in this segment → ([(form, eid)], {eid: rendering}).
    Entity ids are the terminology row ids (stable across runs, visible in the
    review UI); without a db the index in the form list is used (experiments)."""
    present = [(f, tr) for f, tr in glossary.items() if f and f in source]
    eids: dict[str, int] = {}
    if db is not None:
        rows = {form: t["id"] for t in db.terminology_for_project(project_id) for form in json.loads(t["source_forms"])}
        eids = {f: rows[f] for f, _ in present if f in rows}
    ids = {}
    for i, (f, _) in enumerate(present):
        ids[f] = eids.get(f, i)
    return [(f, ids[f]) for f, _ in present], {ids[f]: tr for f, tr in present}


def cooccurrence_signature(source_text: str, term: str, all_forms: list[str]) -> frozenset:
    """Other terminology forms co-occurring with `term` in one source segment."""
    return frozenset(f for f in all_forms if f and f != term and f in source_text)


def cluster_by_cooccurrence(segments, term: str, all_forms: list[str], min_cluster: int = 2) -> list[dict]:
    """Group a term's segments by distinct entity contexts (co-occurrence).

    Greedy first-fit: a segment joins the first cluster whose context union it
    shares a term with; otherwise it starts a new cluster. Empty-signature
    segments (no other term present) form their own cluster — they are still a
    distinct context (the bare/isolated use). Returns clusters sorted by size
    (descending), each as {context, segments, segment_ids}; clusters below
    min_cluster are discarded (singletons are noise, not senses).
    """
    clusters: list[dict] = []
    for seg in segments:
        text = seg["source_text"] or "" if isinstance(seg, dict) or hasattr(seg, "__getitem__") else ""
        # sqlite3.Row supports __getitem__ but not .get; normalize access.
        try:
            text = seg["source_text"] or ""
        except Exception:
            text = getattr(seg, "source_text", "") or ""
        try:
            seg_id = seg["id"]
        except Exception:
            seg_id = getattr(seg, "id", None)
        sig = cooccurrence_signature(text, term, all_forms)
        placed = False
        for cl in clusters:
            if sig and (sig & cl["context"]):
                cl["context"] = cl["context"] | sig
                cl["segments"].append(seg)
                if seg_id is not None:
                    cl["segment_ids"].append(seg_id)
                placed = True
                break
            if not sig and not cl["context"]:
                cl["segments"].append(seg)
                if seg_id is not None:
                    cl["segment_ids"].append(seg_id)
                placed = True
                break
        if not placed:
            clusters.append({
                "context": set(sig),
                "segments": [seg],
                "segment_ids": [seg_id] if seg_id is not None else [],
            })
    kept = [c for c in clusters if len(c["segments"]) >= min_cluster]
    kept.sort(key=lambda c: -len(c["segments"]))
    for c in kept:
        c["context"] = sorted(c["context"])  # json-friendly
    return kept
