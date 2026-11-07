"""Entity-aware retrieval: long-range recall, hard budgets, latency.

Fixture (deterministic, no model): chapter 1's event segment and chapter 3's
query share an entity (Maximilian Copperfield / Max) but have ZERO token
overlap. Old token+recency retrieval ranked chapter 1 last (recall 0/1);
entity-aware retrieval must put it in top-k. Recall before rewrite: 0.
After: 1 (see test_long_range_entity_recall).
"""
import json
import time

from babelink.context import ContextItem, _tokenize, build_context, gather_context, render_context
from babelink.db import Database
from babelink.importers.base import store_chapters
from babelink.models import TerminologyEntry
from babelink.segment import ParsedChapter, to_html
from babelink.story_bible import save_section

CH1 = "Maximilian Copperfield swore the blood oath beneath the rusted lighthouse."
QUERY = "Max watched harbour lights and chose to stay silent."
FILLERS = [
    "Farmers tended grain fields beside the river valley.",
    "Children carried baskets along the dusty mountain path.",
    "Sailors mended nets upon the weathered wooden pier.",
]
TERMS = [{"source_forms": ["Maximilian Copperfield", "Max"], "canonical_translation": {"en": "Max Copperfield"}}]


def _seed_bible(tmp_path):
    save_section(tmp_path, "terminology", TERMS)
    save_section(tmp_path, "chapter_summaries", [
        {"title": "Chapter 1", "summary": "Copperfield swore an oath at the lighthouse."},
        {"title": "Chapter 2", "summary": "Farmers worked the valley."},
    ])
    save_section(tmp_path, "arc_summaries", [{"arc": 1, "summary": "Early oath."}])


def test_long_range_entity_recall(tmp_path):
    """Chapter-3 query with zero token overlap must retrieve chapter-1 event."""
    assert _tokenize(CH1) & _tokenize(QUERY) == set(), "fixture must have zero token overlap"
    _seed_bible(tmp_path)
    history = [CH1] + (FILLERS * 10)  # 30 fillers separate ch1 from query
    recent = history[-3:]
    items = gather_context(str(tmp_path), QUERY, recent, "", max_items=5, all_segments=history)
    top = [i.text for i in items]
    assert CH1 in top, f"chapter-1 segment not in top-5: {top!r}"
    # entity present -> boosted bible_term
    assert any(i.kind == "bible_term" and "Maximilian Copperfield" in i.text for i in items)
    # bible summaries pulled via load_section (chapter + arc)
    items8 = gather_context(str(tmp_path), QUERY, recent, "", max_items=8, all_segments=history)
    texts8 = [i.text for i in items8]
    assert "Copperfield swore an oath at the lighthouse." in texts8
    assert "Early oath." in texts8
    # legacy call without all_segments still works (recent pool only)
    legacy = gather_context(str(tmp_path), QUERY, recent, "", max_items=5)
    assert any(i.kind == "previous_segment" for i in legacy)


def test_build_context_db_long_range(tmp_path):
    """Same zero-overlap fixture through the DB/pipeline path (build_context)."""
    assert _tokenize(CH1) & _tokenize(QUERY) == set()
    db = Database(tmp_path / "p.db")
    pid = db.create_project("t", source_lang="en", target_lang="en")
    doc = db.add_document(pid, "x", "eval", "x")
    ch2_paras = FILLERS * 3  # 9 filler segments between ch1 and query
    cids = store_chapters(db, pid, doc, [
        ParsedChapter("Chapter 1", to_html([CH1]), [CH1]),
        ParsedChapter("Chapter 2", to_html(ch2_paras), ch2_paras),
        ParsedChapter("Chapter 3", to_html([QUERY]), [QUERY]),
    ])
    db.add_terminology(TerminologyEntry(None, pid, ["Maximilian Copperfield", "Max"],
                                        {"en": "Max Copperfield"}, "character", "preferred", "confirmed", 5))
    for cid in cids[:2]:
        for s in db.segments_for_chapter(cid):
            db.conn.execute("UPDATE segments SET translated_text=?, state='TRANSLATED' WHERE id=?",
                            (f"T:{s['source_text']}", s["id"]))
    db.conn.execute("UPDATE chapters SET summary=? WHERE id=?",
                    ("Copperfield swore an oath at the lighthouse.", cids[0]))
    db.conn.execute("UPDATE chapters SET summary=? WHERE id=?", ("Farmers worked the valley.", cids[1]))
    db.conn.commit()
    qseg = db.segments_for_chapter(cids[2])[0]
    # n_prev=1: recency alone would only see the last filler; entity linking must add ch1.
    ctx = build_context(db, pid, qseg, None, n_prev=1, n_summaries=1, n_entity=3)
    assert CH1[:30] in ctx, f"chapter-1 event missing from build_context: {ctx!r}"
    assert len(ctx) <= 2200
    assert "[Story so far]" in ctx
    # context off stays off (no passages, no summaries)
    assert build_context(db, pid, qseg, None, n_prev=0, n_summaries=0, n_entity=0) == ""
    db.close()


def test_render_context_budget_strict():
    big = [ContextItem("summary", "x" * 500, 2.0),
           ContextItem("bible_term", "y" * 500, 4.0),
           ContextItem("previous_segment", "z" * 20, 1.0)]
    for cap in [0, 1, 10, 100, 500, 4000]:
        assert len(render_context(big, max_chars=cap)) <= cap, f"overflow at cap={cap}"
    # prefixes + separators are counted: old code overflowed by len("[kind] ").
    many = [ContextItem("previous_segment", "s" * 30, 1.0) for _ in range(50)]
    assert len(render_context(many, max_chars=200)) <= 200
    # oversized item is skipped, not fatal: later small items still fit.
    mixed = [ContextItem("previous_segment", "a" * 10, 3.0),
             ContextItem("previous_segment", "b" * 1000, 2.0),
             ContextItem("previous_segment", "c" * 10, 1.0)]
    out = render_context(mixed, max_chars=60)
    assert len(out) <= 60 and "a" * 10 in out and "c" * 10 in out and "b" * 100 not in out


def test_gather_context_perf_1000_segments(tmp_path):
    """O(present x segments): 1000 candidates + 2000 irrelevant terms < 200ms."""
    many_terms = [{"source_forms": [f"Term{i} Alpha{i}"], "canonical_translation": {"en": f"T{i}"}}
                  for i in range(2000)]
    save_section(tmp_path, "terminology", many_terms + TERMS)
    save_section(tmp_path, "chapter_summaries", [{"title": "Chapter 1", "summary": "Oath."}])
    segs = [f"Filler segment number {i} with neutral words alpha beta gamma delta." for i in range(1000)]
    segs[0] = CH1
    # warm the JSON loader once, then time a single retrieval (min of 3 to dodge CI jitter).
    gather_context(str(tmp_path), QUERY, segs[-3:], "", max_items=5, all_segments=segs)
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        items = gather_context(str(tmp_path), QUERY, segs[-3:], "", max_items=5, all_segments=segs)
        times.append(time.perf_counter() - t0)
    assert min(times) < 0.2, f"gather_context too slow: {[round(t * 1000, 1) for t in times]} ms"
    assert CH1 in [i.text for i in items], "recall must hold even at 1000 segments"


def test_entity_present_forms_literal(tmp_path):
    """Only literally-present forms produce bible_term; absent terms do not."""
    save_section(tmp_path, "terminology",
                 TERMS + [{"source_forms": ["Unrelated Zebra"], "canonical_translation": {"en": "Zebra"}}])
    items = gather_context(str(tmp_path), QUERY, [], "", max_items=8)
    assert any(i.kind == "bible_term" and "Max" in i.text for i in items)
    assert not any("Unrelated Zebra" in i.text for i in items)
    # malformed/empty terminology section never breaks retrieval
    save_section(tmp_path, "terminology", [])
    assert gather_context(str(tmp_path), QUERY, [CH1], "", max_items=2)
    assert json.dumps("ok")
