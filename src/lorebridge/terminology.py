"""Progressive terminology (spec §15, §79): mine -> LLM classify/translate -> user accepts/edits/ignores.

Status flow: suggested -> confirmed | ignored.  Only non-ignored terms with a translation reach prompts.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from .story_bible import load_section, save_section

_CJK_RUN = re.compile(r"[一-鿿]{2,}")
_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’-]*|[.!?]+|[^\sA-Za-z]")

# function characters: an n-gram that starts/ends with one is a phrase fragment, not a term
_EDGE_STOP = set("的了是在不有和就都而及与着或没我你他她它们这那也很把被让从到对说道为以之其所然后又还只能会要去来可么什吗呢吧啊哦嗯便见")
_INNER_STOP = set("的了是在着把被让也而")  # never inside a name/term
_STOP_WORDS = {
    "自己", "什么", "知道", "没有", "一个", "这个", "那个", "他们", "我们", "你们", "她们", "不是", "就是", "可以", "因为",
    "所以", "如果", "但是", "已经", "现在", "时候", "这样", "那样", "一下", "起来", "出来", "下来", "过来", "看到", "觉得",
    "还是", "只是", "不过", "于是", "这里", "那里", "怎么", "为什么", "东西", "一些", "一样", "似乎", "忽然", "立刻", "终于",
}


def _cjk_candidates(texts: list[str]) -> Counter:
    counts: Counter[str] = Counter()
    for text in texts:
        for run in _CJK_RUN.findall(text):
            for n in (2, 3, 4):
                for i in range(len(run) - n + 1):
                    gram = run[i:i + n]
                    if gram[0] in _EDGE_STOP or gram[-1] in _EDGE_STOP or gram in _STOP_WORDS or _INNER_STOP & set(gram):
                        continue
                    counts[gram] += 1
    return counts


def _latin_candidates(texts: list[str]) -> Counter:
    """Capitalized word runs, ignoring a lone capitalized sentence opener ("The", "He")."""
    counts: Counter[str] = Counter()
    for text in texts:
        toks = _LATIN_TOKEN.findall(text)
        i, sentence_start = 0, True
        while i < len(toks):
            tok = toks[i]
            if tok[0].isupper() and tok[1:2].islower():
                j = i
                while j + 1 < len(toks) and toks[j + 1][:1].isupper() and toks[j + 1][1:2].islower():
                    j += 1
                if j > i or not sentence_start:
                    counts[" ".join(toks[i:j + 1])] += 1
                i, sentence_start = j + 1, False
                continue
            sentence_start = tok[0] in ".!?" or (sentence_start and not tok[0].isalnum())
            i += 1
    return counts


def mine_terms(texts: list[str], min_count: int = 3, top: int = 50) -> list[tuple[str, int]]:
    counts = _cjk_candidates(texts) + _latin_candidates(texts)
    cand = {t: c for t, c in counts.items() if c >= min_count}
    # extension index: for each candidate, the best count among candidates that extend it by one char/word
    ext: dict[str, int] = {}
    for t, c in cand.items():
        if " " in t:
            parts = t.split(" ")
            subs = [" ".join(parts[1:]), " ".join(parts[:-1])] if len(parts) > 1 else []
        else:
            subs = [t[1:], t[:-1]] if len(t) > 2 else []
        for s in subs:
            if s:
                ext[s] = max(ext.get(s, 0), c)
    keep: list[tuple[str, int]] = []
    for t, c in cand.items():
        if ext.get(t, 0) >= 0.7 * c:  # fragment of a longer, equally frequent term
            continue
        if " " in t:
            parts = t.split(" ")
            subs = [" ".join(parts[1:]), " ".join(parts[:-1])]
        else:
            subs = [t[1:], t[:-1]] if len(t) > 2 else []
        if any(s in cand and c < 0.3 * cand[s] for s in subs):  # rare extension of a common term
            continue
        keep.append((t, c))
    keep.sort(key=lambda x: (-x[1], -len(x[0]), x[0]))
    return keep[:top]


# ---- glossary / DB-backed flow ------------------------------------------------------------------
def build_glossary(db, project_id: int) -> dict[str, str]:
    """source form -> target rendering, for every non-ignored term that has one."""
    target = db.get_project(project_id)["target_lang"]
    glossary: dict[str, str] = {}
    for t in db.terminology_for_project(project_id):
        if t["status"] == "ignored":
            continue
        tr = json.loads(t["canonical_translation"]).get(target)
        if tr:
            for form in json.loads(t["source_forms"]):
                glossary[form] = tr
    return glossary


def upsert_candidates(db, project_id: int, candidates: list[tuple[str, int]]) -> int:
    """Add new candidates; refresh occurrence counts of known ones. Returns number added."""
    from .models import TerminologyEntry

    known: dict[str, int] = {}
    for t in db.terminology_for_project(project_id):
        for f in json.loads(t["source_forms"]):
            known[f] = t["id"]
    added = 0
    for term, count in candidates:
        if term in known:
            db.update_terminology(known[term], occurrences=count)
        else:
            db.add_terminology(TerminologyEntry(None, project_id, [term], {}, "", "preferred", "suggested", count))
            added += 1
    return added


def _example(db, project_id: int, term: str) -> str:
    row = db.conn.execute("SELECT source_text FROM segments WHERE project_id=? AND instr(source_text, ?)>0 ORDER BY length(source_text) LIMIT 1",
                          (project_id, term)).fetchone()
    return row["source_text"] if row else ""


def _is_common_word(translation: str) -> bool:
    """A cased rendering that starts lower-case ("waste", "a", "body") is an ordinary word, not a name or fixed term.
    Enforcing it would rewrite normal prose ("this waste die?"), so it never enters the glossary."""
    return translation.lower() != translation.upper() and not translation[:1].isupper()


def suggest_term_translations(db, project_id: int, provider, *, batch: int = 12, limit: int = 60,
                              only_forms: set[str] | None = None) -> int:
    """One LLM call per `batch` untranslated candidates: keep real terms, classify, translate.
    Candidates the model drops, or renders as a lower-case common word, become `ignored` (only when the batch
    produced a parsable answer)."""
    project = db.get_project(project_id)
    target = project["target_lang"]
    todo = [t for t in db.terminology_for_project(project_id)
            if t["status"] == "suggested" and target not in json.loads(t["canonical_translation"])
            and (only_forms is None or only_forms & set(json.loads(t["source_forms"])))][:limit]
    done = 0
    for i in range(0, len(todo), batch):
        rows = todo[i:i + batch]
        by_term = {json.loads(r["source_forms"])[0]: r for r in rows}
        found = provider.suggest_terms([(t, _example(db, project_id, t)) for t in by_term], project["source_lang"], target)
        if not found:
            continue
        seen = set()
        for f in found:
            row = by_term.get(str(f["term"]).strip())
            tr = str(f["translation"]).strip()
            if not row or _is_common_word(tr):
                continue
            seen.add(row["id"])
            canon = json.loads(row["canonical_translation"])
            canon[target] = tr
            db.update_terminology(row["id"], canonical_translation=canon, concept=str(f.get("type", "")).strip().lower())
            done += 1
        for row in rows:
            if row["id"] not in seen:
                db.update_terminology(row["id"], status="ignored")
    return done


def set_term(db, project_id: int, term_id: int, translation: str | None = None, status: str | None = None,
             concept: str | None = None) -> None:
    """Accept / edit / ignore a term (user action). Editing a translation marks affected segments STALE."""
    row = next((t for t in db.terminology_for_project(project_id) if t["id"] == term_id), None)
    if row is None:
        raise KeyError(f"term {term_id}")
    target = db.get_project(project_id)["target_lang"]
    fields: dict = {}
    if translation is not None:
        canon = json.loads(row["canonical_translation"])
        changed = canon.get(target) != translation
        canon[target] = translation
        fields["canonical_translation"] = canon
        fields["status"] = "confirmed"
    if status:
        fields["status"] = status
    if concept is not None:
        fields["concept"] = concept
    db.update_terminology(term_id, **fields)
    if translation is not None and changed:
        from .models import SegmentState

        for form in json.loads(row["source_forms"]):
            for s in db.conn.execute("SELECT id FROM segments WHERE project_id=? AND instr(source_text, ?)>0 AND translated_text IS NOT NULL "
                                     "AND state NOT IN ('HUMAN_APPROVED','STALE')", (project_id, form)).fetchall():
                db.update_segment(s["id"], state=SegmentState.STALE)


_BIBLE_SECTION = {"character": "characters", "location": "locations", "organization": "organizations", "item": "items",
                  "technique": "abilities", "realm": "abilities"}


def sync_bible(db, project_id: int, project_dir: str) -> None:
    """Mirror the terminology table into Story Bible JSON. Entries without origin=auto are user-owned: untouched."""
    target = db.get_project(project_id)["target_lang"]
    sections: dict[str, list[dict]] = {k: [] for k in ("terminology", *set(_BIBLE_SECTION.values()))}
    for t in db.terminology_for_project(project_id):
        if t["status"] == "ignored":
            continue
        forms = json.loads(t["source_forms"])
        canon = json.loads(t["canonical_translation"])
        entry = {"origin": "auto", "source_forms": forms, "canonical_translation": canon, "concept": t["concept"],
                 "policy": t["policy"], "status": t["status"], "occurrences": t["occurrences"]}
        sections["terminology"].append(entry)
        if target in canon and t["concept"] in _BIBLE_SECTION:
            sections[_BIBLE_SECTION[t["concept"]]].append(
                {"origin": "auto", "source": forms[0], "translation": canon[target], "type": t["concept"],
                 "status": "confirmed" if t["status"] == "confirmed" else "inferred", "occurrences": t["occurrences"]})
    def key(e: dict) -> str:
        return e.get("source") or (e.get("source_forms") or [""])[0]

    for name, entries in sections.items():
        old = load_section(project_dir, name)
        old = [e for e in old if isinstance(e, dict)] if isinstance(old, list) else []
        prev = {key(e): e for e in old}
        merged = [{**{k: v for k, v in prev.get(key(e), {}).items() if k not in e}, **e} for e in entries]  # keep user notes
        save_section(project_dir, name, merged + [e for e in old if e.get("origin") != "auto"])


def detect_contradictions(db, project_id: int, project_dir: str) -> list[dict]:
    """Same source form with different canonical translations (spec §25). Rewrites the section: no duplicates."""
    from collections import defaultdict

    seen: dict[str, set[str]] = defaultdict(set)
    for row in db.terminology_for_project(project_id):
        if row["status"] == "ignored":
            continue
        canon = json.loads(row["canonical_translation"])
        target = next(iter(canon.values()), "")
        if target:
            for f in json.loads(row["source_forms"]):
                seen[f].add(target)
    conflicts = [{"type": "terminology_drift", "source_form": form, "translations": sorted(ts), "severity": "warning"}
                 for form, ts in seen.items() if len(ts) > 1]
    save_section(project_dir, "contradictions", conflicts)
    return conflicts
