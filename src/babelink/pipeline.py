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
from .eval import term_adherence
from .fallback import ChainProvider
from .langdetect import detect_language
from .models import ModelRun, SegmentState, TMEntry
from .prompts import RefusalError
from .qa import check_segment, qa_segment
from .summaries import write_summaries
from .terminology import (build_glossary, demote_unused_terms, detect_contradictions, finalize_terms, measure_drift, mine_terms,
                           review_confirmed_terms, suggest_term_translations, sync_bible, upsert_candidates)

_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")
class Stopped(Exception):
    """Raised inside a streaming callback to abandon the paragraph being written when the user pressed Stop."""


Progress = Callable[[int, int, str], None]
TextEvent = Callable[[object, str, bool], None]  # (segment row, text so far, final) - lets a UI show a translation as it is written
_WORD = re.compile(r"[一-鿿぀-ヿ가-힯]|[A-Za-z0-9À-ɏ]+(?:['’-][A-Za-z0-9À-ɏ]+)*")
ALL_FEATURES = frozenset({"glossary", "bible", "tm", "context", "placeholders"})  # switchable for the A-G experiments (spec §57)
# placeholders: entity-first masking (entities.py) INSTEAD of glossary hints —
# masked names are restored post-draw, so prompt hints are off in that mode.


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


def _engine(provider):
    """The translating engine for capability decisions: unwrap a failover
    chain to its primary. Returns the provider when it is translation-only
    (MT cross-checks apply), else None."""
    p = provider.primary if isinstance(provider, ChainProvider) else provider
    return p if getattr(p, "translation_only", False) else None


def analyze_project(db: Database, project_id: int, project_dir: Optional[str] = None, provider=None, term_provider=None) -> dict:
    """Detect language, mine terminology (incremental), refresh summaries/Bible, mark segments ANALYZED.
    With a provider, candidates are also classified + translated by the model (one call per batch).
    `term_provider` (default: `provider`) is the model for term classification: pass the general-LLM
    helper here when `provider` is a translation-only model (Hy-MT2)."""
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
    translator = _engine(provider)
    translated_terms = suggest_term_translations(db, project_id, term_provider or provider, translator=translator) if (term_provider or provider) else 0
    db.conn.execute("UPDATE segments SET state='ANALYZED' WHERE project_id=? AND state='UNPROCESSED'", (project_id,))
    db.conn.commit()
    if project_dir:
        sync_bible(db, project_id, project_dir)
        write_summaries(db, project_id, project_dir)
        detect_contradictions(db, project_id, project_dir)
    return {"language": lang, "segments": len(segs), "terms": added, "terms_translated": translated_terms}


def _version(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]


def _repetition(draft: str) -> int:
    """Worst repeated 5-gram count minus one: catches model stutter loops."""
    words = draft.split()
    if len(words) < 6:
        return 0
    counts: dict[tuple[str, ...], int] = {}
    for i in range(len(words) - 4):
        gram = tuple(words[i:i + 5])
        counts[gram] = counts.get(gram, 0) + 1
    return max(counts.values()) - 1


def select_best(source: str, drafts: list[str], hits: dict[str, str], source_lang: str) -> str:
    """Pick the best of several model draws, deterministically: most glossary
    terms applied, then fewest QA errors, then least source-script leftover,
    then least repetition. Ties keep the first draw (stable)."""
    def key(d: str):
        hb, _ = term_adherence(source, d, hits) if hits else (0, 0)
        errs = sum(1 for sev, _, _ in check_segment(source, d, source_lang, hits) if sev == "error")
        cjk = sum(1 for p in d.split("\n\n") if _CJK.search(p))
        return (hb, -errs, -cjk, -_repetition(d))

    return max(drafts, key=key)


def _has_untranslated_terms(db: Database, project_id: int) -> bool:
    """Any mined candidate still waiting for a rendering (so auto-suggest has work)."""
    return db.conn.execute(
        "SELECT 1 FROM terminology WHERE project_id=? AND status='suggested' AND canonical_translation='{}' LIMIT 1",
        (project_id,)).fetchone() is not None


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
                      word_limit: Optional[int] = None, on_text: Optional[TextEvent] = None,
                      summarize_provider=None, candidates: int = 1, split_pieces: int = 300) -> int:
    """Translate open segments in reading order. Returns the number translated this call.

    - exact TM hit (approved first) is reused; fuzzy TM hits are passed as prompt hints
    - glossary = confirmed/suggested terms present in the segment
    - `candidates`: model draws per segment; the deterministic best wins
      (most terms applied, fewest QA errors, least leftover/repetition). When a
      draw still misses a glossary term, one guided retry fires (max 3 calls).
      More calls, best possible result; 1 keeps today’s speed.
    - refusals flag the segment NEEDS_REVIEW and continue (no jailbreak retries, spec §63)
    - provider errors abort the run; progress is already saved
    - `features` (default: all of ALL_FEATURES) lets experiments switch layers off
    - `split_pieces` (E13): segments longer than this many chars are translated
      as sentence pieces sharing one kw (glossary hits from the FULL segment,
      so names stay consistent), then rejoined and stored as one segment.
      0 = off. Counters truncation + clause drops on long paragraphs. Default
      300 (DH19: +1.2 chrF vs off, same 13/13 terms, faster on CPU).
    - `word_limit` stops after the first paragraph that brings the batch to that many words (`take_words`)
    - `on_text(segment, text_so_far, final)` fires when a paragraph starts (empty text), while the model writes it
      (if the provider streams) and once more, final, after it is saved
    """
    feats = ALL_FEATURES if features is None else frozenset(features)
    project = db.get_project(project_id)
    glossary = build_glossary(db, project_id)
    if "glossary" in feats and not glossary and _has_untranslated_terms(db, project_id):
        # Upload-and-go: nobody ran analyze+suggest yet. The helper (or a
        # general translator) classifies once; an MT-only translator alone
        # cannot, so glossary stays empty rather than filling with junk.
        suggester = next((c for c in (summarize_provider, provider) if c is not None
                          and not getattr(c, "translation_only", False)), None)
        if suggester is not None:
            segs = db.open_segments(project_id, chapter_id)
            present = {f for t in db.terminology_for_project(project_id) for f in json.loads(t["source_forms"])
                       if any(f in s["source_text"] for s in segs)}
            translator = _engine(provider)
            suggest_term_translations(db, project_id, suggester, limit=60, retry_present=present, translator=translator)
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

    def _mt(text: str, kw: dict, seg, on_delta) -> str:
        """One MT draw, optionally as sentence pieces (E13, split_pieces=N):
        pieces share kw (glossary from the full segment), rejoin to one draft.
        Placeholders ride along inside the text and restore after rejoin."""
        from .segment import split_long

        pieces = split_long(text, split_pieces) if split_pieces else [text]
        if len(pieces) == 1:
            return provider.translate(text, on_delta=on_delta, **kw) if on_delta else provider.translate(text, **kw)
        outs = [provider.translate(p, **kw) if on_delta is None else provider.translate(p, on_delta=on_delta, **kw)
                for p in pieces]
        glue = "" if _CJK.search(text) else " "
        return glue.join(o.strip() for o in outs)
    titled: set[int] = set()
    done = skipped = 0
    done_ids: list[int] = []
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
            hits = {k: v for k, v in glossary.items() if k in s["source_text"]} if {"glossary", "placeholders"} & feats else {}
            use_tm = "tm" in feats and s["state"] != SegmentState.STALE.value
            tm = db.tm_lookup(project_id, s["source_text"]) if use_tm else None
            new_tm = False
            mask_map: dict[int, str] = {}
            if "placeholders" in feats and hits:
                # Entity-first: opaque proxies instead of ignorable prompt hints.
                from .entities import entities_for_segment, mask_entities, restore_proxies

                _ents, mask_map = entities_for_segment(hits, s["source_text"], db, project_id)
                masked_src, used = mask_entities(s["source_text"], _ents)
                _mask = (masked_src, used)
                hits = {}  # restored drafts carry the renderings; no hints needed
            else:
                _mask = None
            src_for_mt = _mask[0] if _mask else s["source_text"]
            if tm:
                translated, stats["tm_exact"] = tm["translated_text"], stats["tm_exact"] + 1
            else:
                ctx_on = "context" in feats
                kw = dict(glossary=hits if "glossary" in feats else {}, style=style, source_lang=project["source_lang"], target_lang=project["target_lang"],
                          context=build_context(db, project_id, s, project_dir if "bible" in feats else None,
                                                n_prev=n_prev if ctx_on else 0, n_summaries=2 if ctx_on else 0),
                          tm_hints=[(r["source_text"], r["translated_text"]) for _, r in db.tm_similar(project_id, s["source_text"])] if "tm" in feats else [])
                def delta(text: str, s=s) -> None:  # Stop takes effect mid-paragraph: leaving the stream cancels the model's generation
                    if should_stop and should_stop():
                        raise Stopped
                    on_text(s, text, False)

                try:
                    first = _mt(src_for_mt, kw, s, delta if on_text else None)
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
                drafts = [first]
                for _ in range(max(1, candidates) - 1):  # extra draws for selection
                    try:
                        drafts.append(_mt(src_for_mt, kw, s, None))
                        stats["extra_calls"] = stats.get("extra_calls", 0) + 1
                    except (Stopped, RefusalError):
                        break
                if _mask:
                    from .entities import restore_proxies as _restore

                    drafts = [_restore(d, mask_map)[0] for d in drafts]
                translated = select_best(s["source_text"], drafts, hits, project["source_lang"])
                missing = any(k in s["source_text"] and v not in translated for k, v in hits.items())
                if _mask:
                    # A proxy the model swallowed (not emitted) is a missing
                    # name by construction — retry like a missed hint.
                    missing = missing or any(p not in translated for p in _mask[1].values())
                leftover = bool(_CJK.search(s["source_text"]) and _CJK.search(translated))
                if (missing or leftover) and len(drafts) < 3:
                    try:  # guided retry: one more draw when a term is missing or source script is left behind;
                        _retry = _mt(src_for_mt, kw, s, None)  # select_best keeps the cleaner draw
                        if _mask:
                            from .entities import restore_proxies as _restore2

                            _retry = _restore2(_retry, mask_map)[0]
                        drafts.append(_retry)
                        translated = select_best(s["source_text"], drafts, hits, project["source_lang"])
                    except (Stopped, RefusalError):
                        pass
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
            done_ids.append(s["id"])
            stats["segments"] = done
            if on_text:
                on_text(s, translated, True)
            if on_progress:
                on_progress(done + skipped + stats["refusals"], len(pending), s["source_text"][:60])
            if summarize and (not should_stop or not should_stop()):
                _maybe_summarize(db, ch["id"], summarize_provider or provider, project)
        if done:
            if "glossary" in feats:  # vote + normalize need enforced terms; bare runs stay pure baselines
                stats["elected"] = finalize_terms(db, project_id, done_ids, project["target_lang"])
                stats["drift"] = measure_drift(db, project_id)["score"]
                stats["revise_review"] = review_confirmed_terms(db, project_id)
            stats["demoted"] = demote_unused_terms(db, project_id)
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
