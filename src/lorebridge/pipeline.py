"""End-to-end pipeline: analyze + translate with checkpointing (spec §28, §41).

Every segment is committed as soon as it is translated, so a crash/stop/Ctrl-C loses nothing and a
re-run resumes at the first open segment. Pasting a new chapter and running the same functions
continues the project: glossary, TM, Bible notes, summaries and the previous translated passages
of earlier chapters all feed the next prompts.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Callable, Optional

from . import prompts
from .context import build_context
from .db import OPEN_STATES, Database
from .langdetect import detect_language
from .models import ModelRun, SegmentState, TMEntry
from .prompts import RefusalError
from .qa import check_segment, qa_segment
from .summaries import write_summaries
from .terminology import (build_glossary, detect_contradictions, mine_terms, suggest_term_translations, sync_bible,
                          upsert_candidates)

_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")
class Stopped(Exception):
    """Raised inside a streaming callback to abandon the paragraph being written when the user pressed Stop."""


Progress = Callable[[int, int, str], None]
TextEvent = Callable[[object, str, bool], None]  # (segment row, text so far, final) - lets a UI show a translation as it is written
_WORD = re.compile(r"[一-鿿぀-ヿ가-힯]|[A-Za-z0-9À-ɏ]+(?:['’-][A-Za-z0-9À-ɏ]+)*")
ALL_FEATURES = frozenset({"glossary", "bible", "tm", "context"})  # switchable for the A-F experiments (spec §57)


def count_words(text: str) -> int:
    """Words for a batch limit: one per CJK character (there are no spaces to split on), one per run of letters or digits otherwise."""
    return len(_WORD.findall(text or ""))


def take_words(segments: list, limit: Optional[int]) -> list:
    """Leading segments holding at least `limit` words (a paragraph is never cut); always one when any exist."""
    if not limit or limit <= 0:
        return segments
    out, total = [], 0
    for s in segments:
        out.append(s)
        total += count_words(s["source_text"])
        if total >= limit:
            break
    return out


def analyze_project(db: Database, project_id: int, project_dir: Optional[str] = None, provider=None) -> dict:
    """Detect language, mine terminology (incremental), refresh summaries/Bible, mark segments ANALYZED.
    With a provider, candidates are also classified + translated by the model (one call per batch)."""
    segs = db.conn.execute(
        "SELECT s.* FROM segments s JOIN chapters c ON c.id=s.chapter_id WHERE s.project_id=? ORDER BY c.order_index, s.order_index",
        (project_id,),
    ).fetchall()
    if not segs:
        return {"language": "und", "segments": 0, "terms": 0}
    project = db.get_project(project_id)
    lang = project["source_lang"]
    if lang in ("auto", "und", "", None):
        lang = detect_language("\n".join(s["source_text"] for s in segs[:200]))
        db.set_project(project_id, source_lang=lang)
    added = upsert_candidates(db, project_id, mine_terms([s["source_text"] for s in segs], min_count=3, top=80))
    translated_terms = suggest_term_translations(db, project_id, provider) if provider else 0
    for s in segs:
        if s["state"] == SegmentState.UNPROCESSED.value:
            db.update_segment(s["id"], state=SegmentState.ANALYZED)
    if project_dir:
        sync_bible(db, project_id, project_dir)
        write_summaries(db, project_id, project_dir)
        detect_contradictions(db, project_id, project_dir)
    return {"language": lang, "segments": len(segs), "terms": added, "terms_translated": translated_terms}


def _version(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]


def _translate_title(db: Database, ch, provider, glossary: dict, project) -> None:
    if ch["title_translated"] or not _CJK.search(ch["title"] or ""):
        return
    try:
        t = provider.translate(ch["title"], glossary=glossary, max_tokens=96, style="literal",
                               source_lang=project["source_lang"], target_lang=project["target_lang"])
    except (RefusalError, prompts.ProviderError):
        return
    t = t.strip().splitlines()[0].strip(" \"'“”") if t.strip() else ""
    if t:
        db.set_chapter_title_translated(ch["id"], t)


def translate_project(db: Database, project_id: int, provider, *, limit: Optional[int] = None,
                      project_dir: Optional[str] = None, chapter_id: Optional[int] = None,
                      on_progress: Optional[Progress] = None, should_stop: Optional[Callable[[], bool]] = None,
                      summarize: bool = False, escalate_provider=None, n_prev: int = 3,
                      features: Optional[frozenset[str]] = None, only_ids: Optional[set[int]] = None,
                      word_limit: Optional[int] = None, on_text: Optional[TextEvent] = None) -> int:
    """Translate open segments in reading order. Returns the number translated this call.

    - exact TM hit (approved first) is reused; fuzzy TM hits are passed as prompt hints
    - glossary = confirmed/suggested terms present in the segment
    - refusals flag the segment NEEDS_REVIEW and continue (no jailbreak retries, spec §63)
    - provider errors abort the run; progress is already saved
    - `features` (default: all of ALL_FEATURES) lets experiments switch layers off
    - `word_limit` stops after the first paragraph that brings the batch to that many words (`take_words`)
    - `on_text(segment, text_so_far, final)` fires when a paragraph starts (empty text), while the model writes it
      (if the provider streams) and once more, final, after it is saved
    """
    feats = ALL_FEATURES if features is None else frozenset(features)
    project = db.get_project(project_id)
    glossary = build_glossary(db, project_id)
    style = project["style"] or "faithful-natural"
    pending = db.open_segments(project_id, chapter_id)
    if only_ids is not None:
        pending = [s for s in pending if s["id"] in only_ids]
    if limit is not None:
        pending = pending[:limit]
    pending = take_words(pending, word_limit)
    params = {"style": style, "n_prev": n_prev, "chapter_id": chapter_id}
    run_id = db.add_model_run(ModelRun(None, project_id, "translation", provider.name, prompts.PROMPT_VERSION,
                                       _version(sorted(glossary.items())), "", "", json.dumps(params)))
    stats = {"segments": 0, "tm_exact": 0, "refusals": 0, "escalated": 0, "skipped": 0}
    titled: set[int] = set()
    done = skipped = 0
    try:
        for s in pending:
            if should_stop and should_stop():
                break
            if not db.segment_is_open(s["id"]):  # edited, approved or translated since the batch was picked: leave it
                skipped += 1
                stats["skipped"] = skipped
                continue
            ch = db.conn.execute("SELECT * FROM chapters WHERE id=?", (s["chapter_id"],)).fetchone()
            if on_text:
                on_text(s, "", False)
            if ch["id"] not in titled:
                titled.add(ch["id"])
                _translate_title(db, ch, provider, glossary if "glossary" in feats else {}, project)
            hits = {k: v for k, v in glossary.items() if k in s["source_text"]} if "glossary" in feats else {}
            use_tm = "tm" in feats and s["state"] != SegmentState.STALE.value
            tm = db.tm_lookup(project_id, s["source_text"]) if use_tm else None
            new_tm = False
            if tm:
                translated, stats["tm_exact"] = tm["translated_text"], stats["tm_exact"] + 1
            else:
                ctx_on = "context" in feats
                kw = dict(glossary=hits, style=style, source_lang=project["source_lang"], target_lang=project["target_lang"],
                          context=build_context(db, project_id, s, project_dir if "bible" in feats else None,
                                                n_prev=n_prev if ctx_on else 0, n_summaries=2 if ctx_on else 0),
                          tm_hints=[(r["source_text"], r["translated_text"]) for _, r in db.tm_similar(project_id, s["source_text"])] if "tm" in feats else [])
                def delta(text: str, s=s) -> None:  # Stop takes effect mid-paragraph: leaving the stream cancels the model's generation
                    if should_stop and should_stop():
                        raise Stopped
                    on_text(s, text, False)

                try:
                    translated = provider.translate(s["source_text"], on_delta=delta, **kw) if on_text else provider.translate(s["source_text"], **kw)
                except Stopped:
                    break
                except RefusalError as exc:
                    stats["refusals"] += 1
                    if db.update_segment(s["id"], state=SegmentState.NEEDS_REVIEW, model_run_id=run_id, only_if_state=OPEN_STATES):
                        db.clear_qa(s["id"], ("refusal",))
                        db.add_qa(project_id, s["id"], "error", "refusal", f"Model refused: {exc}")
                    if on_text:
                        on_text(s, "", True)
                    if on_progress:
                        on_progress(done + skipped + stats["refusals"], len(pending), s["source_text"][:60])
                    continue
                if escalate_provider is not None and escalate_provider is not provider:
                    translated = _escalate(escalate_provider, s, translated, hits, kw, project, stats)
                new_tm = True
            qa_segment(db, project_id, s["id"], s["source_text"], translated, project["source_lang"], hits)
            blocking = db.conn.execute("SELECT 1 FROM qa_results WHERE segment_id=? AND severity='error'", (s["id"],)).fetchone()
            if not db.update_segment(s["id"], translated=translated, model_run_id=run_id, only_if_state=OPEN_STATES,
                                     state=SegmentState.NEEDS_REVIEW if blocking else SegmentState.TRANSLATED):
                db.clear_qa(s["id"])  # a person edited or approved it while the model was writing: their text stays, ours is dropped
                skipped += 1
                stats["skipped"] = skipped
                if on_text:
                    on_text(s, (db.get_segment(s["id"]) or {"translated_text": ""})["translated_text"] or "", True)
                continue
            if new_tm:
                db.add_tm(TMEntry(None, project_id, s["source_text"], translated, model=provider.name, chapter_id=s["chapter_id"]))
            done += 1
            stats["segments"] = done
            if on_text:
                on_text(s, translated, True)
            if on_progress:
                on_progress(done + skipped + stats["refusals"], len(pending), s["source_text"][:60])
            if summarize and (not should_stop or not should_stop()):
                _maybe_summarize(db, ch["id"], provider, project)
    finally:
        stats["usage"] = dict(getattr(provider, "usage", {}))
        db.finish_model_run(run_id, {**params, **stats})
    return done


def _escalate(strong, s, draft: str, hits: dict, kw: dict, project, stats: dict) -> str:
    """Re-translate with the stronger model when deterministic signals say the draft is risky (spec §10)."""
    from .escalation import escalation_needed

    types = [t for _, t, _ in check_segment(s["source_text"], draft, project["source_lang"], hits)]
    need, _ = escalation_needed(s["source_text"], draft, types, len(hits))
    if not need:
        return draft
    try:
        better = strong.translate(s["source_text"], **kw)
    except (RefusalError, prompts.ProviderError):
        return draft
    stats["escalated"] += 1
    return better


def _maybe_summarize(db: Database, chapter_id: int, provider, project) -> None:
    """When the last open segment of a chapter is done, store a model summary for later chapters' context."""
    left = db.conn.execute("SELECT COUNT(*) n FROM segments WHERE chapter_id=? AND translated_text IS NULL", (chapter_id,)).fetchone()["n"]
    if left:
        return
    text = "\n".join(r["translated_text"] for r in db.segments_for_chapter(chapter_id) if r["translated_text"])
    try:
        summary = provider.summarize(text, project["target_lang"])
    except prompts.ProviderError:
        return
    if summary:
        db.conn.execute("UPDATE chapters SET summary=? WHERE id=?", (summary, chapter_id))
        db.conn.commit()
