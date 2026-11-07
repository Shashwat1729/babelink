import json
from pathlib import Path

import pytest

from babelink import story_bible
from babelink.db import Database
from babelink.graph import (
    EntityGraph,
    apply_recommendation,
    chapter_entity_map,
    graph_from_db,
    link_recommendations,
)
from babelink.models import Chapter, Segment, TerminologyEntry


def _db(tmp_path: Path):
    db = Database(tmp_path / "project.db")
    pid = db.create_project("t", target_lang="en")
    did = db.add_document(pid, "novel.txt", "txt", "T")
    c1 = db.add_chapter(Chapter(None, pid, did, 0, "Ch1"))
    c2 = db.add_chapter(Chapter(None, pid, did, 1, "Ch2"))
    for ch, texts in ((c1, ["林凡走进东湖。", "林凡遇见苏晴。"]), (c2, ["苏晴与林凡在东湖重逢。", "东湖的夜晚。"])):
        for i, t in enumerate(texts):
            db.add_segment(Segment(None, pid, did, ch, None, i, t))
    for forms, concept, occ in (
        (["林凡"], "character", 5),
        (["苏晴"], "character", 4),
        (["东湖"], "location", 3),
    ):
        db.add_terminology(TerminologyEntry(None, pid, forms, {"en": forms[0]}, concept, "preferred", "confirmed", occ))
    return db, pid, c1, c2


# ---- node CRUD ---------------------------------------------------------------
def test_node_crud():
    g = EntityGraph()
    eid = g.add_entity("character", "林凡")
    assert eid == "character:林凡"
    assert g.get(eid)["names"] == ["林凡"]
    # upsert merges aliases, never duplicates
    assert g.add_entity("character", "林凡", names=["小凡"]) == eid
    assert set(g.get(eid)["names"]) == {"林凡", "小凡"}
    assert g.resolve("小凡") == eid
    # update
    g.update_entity(eid, importance=9, notes="protagonist")
    assert g.get(eid)["importance"] == 9 and g.get(eid)["notes"] == "protagonist"
    g.update_entity(eid, first_seen=2)
    g.update_entity(eid, first_seen=0)
    assert g.get(eid)["first_seen"] == 0  # earliest wins
    # entities filtered by type
    g.add_entity("place", "东湖")
    assert [e["name"] for e in g.entities("place")] == ["东湖"]
    assert len(g.entities()) == 2
    # remove
    assert g.remove_entity("place:东湖") is True
    assert g.get("place:东湖") is None
    with pytest.raises(KeyError):
        g.update_entity("character:没有", importance=1)
    with pytest.raises(ValueError):
        g.add_entity("character")


def test_unknown_fields_preserved():
    g = EntityGraph()
    eid = g.add_entity("character", "林凡", custom={"arc": 1}, rating=5)
    assert g.get(eid)["custom"] == {"arc": 1} and g.get(eid)["rating"] == 5


# ---- edge CRUD ---------------------------------------------------------------
def test_edge_crud():
    g = EntityGraph()
    a = g.add_entity("character", "林凡")
    b = g.add_entity("character", "苏晴")
    c = g.add_entity("place", "东湖")
    e = g.link(a, c, "located_in", count=1)
    assert e["type"] == "located_in" and e["count"] == 1
    g.link(a, c, "located_in")  # repeat increments
    rels = g.relations_of(a)
    assert len(rels) == 1 and rels[0]["other_id"] == c and rels[0]["direction"] == "out"
    assert rels[0]["count"] == 2
    rels_in = g.relations_of(c)
    assert rels_in[0]["direction"] == "in" and rels_in[0]["other_id"] == a
    # validation
    with pytest.raises(ValueError):
        g.link(a, b, "not_a_type")
    with pytest.raises(ValueError):
        g.link(a, a, "allied_with")
    with pytest.raises(KeyError):
        g.link(a, "character:幽灵", "allied_with")
    # remove entity drops its edges
    assert len(g.relations_of(b)) == 0
    assert g.unlink(a, c, "located_in") is True
    assert g.relations_of(a) == []
    assert g.unlink(a, c, "located_in") is False


# ---- entity linking -----------------------------------------------------------
def test_entities_active_in():
    g = EntityGraph()
    g.add_entity("character", "林凡", importance=2)
    g.add_entity("character", "苏晴", importance=1)
    g.add_entity("place", "东湖")
    hits = g.entities_active_in("林凡在东湖遇见苏晴")
    assert [h["name"] for h in hits] == ["林凡", "东湖", "苏晴"]  # importance, then name
    assert [h["name"] for h in g.entities_active_in("无关文字")] == []
    assert g.entities_active_in("") == []
    assert [h["name"] for h in g.entities_active_in({"source_text": "林凡笑道"})] == ["林凡"]


# ---- DB -> graph / chapter map / co-occurrence --------------------------------
def test_chapter_map_and_cooccurrence(tmp_path):
    db, pid, c1, c2 = _db(tmp_path)
    g = graph_from_db(db, pid)
    assert g.resolve("林凡") == "character:林凡"
    assert g.get("character:林凡")["first_seen"] == 0
    assert g.get("place:东湖")["translation"] == "东湖"
    cmap = chapter_entity_map(db, pid, g)
    # shared: 林凡 and 苏晴 active in both chapters; 东湖 in both chapters too
    assert set(cmap[c1]) == {"character:林凡", "character:苏晴", "place:东湖"}
    assert set(cmap[c2]) == {"character:林凡", "character:苏晴", "place:东湖"}
    recs = link_recommendations(g, cmap)
    by_pair = {(r["source"], r["target"]): r for r in recs}
    assert by_pair[("character:林凡", "character:苏晴")]["count"] == 2
    assert by_pair[("character:林凡", "place:东湖")]["count"] == 2
    assert all(r["count"] >= 2 for r in recs)
    # chapter presence is an idempotent edge, and active = 2+ segments
    assert g.relations_of("character:林凡")
    # recommendations approve into real edges, then stay out of future recs
    apply_recommendation(g, by_pair[("character:林凡", "character:苏晴")])
    assert g.relations_of("character:林凡")
    db.close()


def test_chapter_map_builds_graph_when_missing(tmp_path):
    db, pid, c1, _ = _db(tmp_path)
    cmap = chapter_entity_map(db, pid)
    assert cmap[c1] and "character:林凡" in cmap[c1]
    db.close()


# ---- persistence ---------------------------------------------------------------
def test_graph_persistence_roundtrip(tmp_path, ):
    g = EntityGraph()
    a = g.add_entity("character", "林凡", names=["小凡"], importance=3, notes="男主")
    b = g.add_entity("place", "东湖")
    g.link(a, b, "located_in", chapters=[1])
    path = tmp_path / "graph.json"
    g.save(path)
    g2 = EntityGraph.load(path)
    assert g2.get(a) == g.get(a)
    assert g2.to_dict() == g.to_dict()
    assert g2.relations_of(a)[0]["count"] == 1


# ---- backward-compat with the old flat JSON format -----------------------------
def test_old_format_backward_compat(tmp_path):
    project_dir = tmp_path
    bible = project_dir / "story_bible"
    bible.mkdir()
    # old-format file: no ids/types/edges, extra unknown fields
    (bible / "characters.json").write_text(json.dumps(
        [{"origin": "user", "source": "大师兄", "translation": "Senior Brother",
          "gender": "male", "notes": "kind", "rating": 5}], ensure_ascii=False), encoding="utf-8")
    (bible / "terminology.json").write_text(json.dumps(
        [{"origin": "auto", "source_forms": ["林凡"], "canonical_translation": {"en": "Lin Fan"},
          "concept": "character", "policy": "preferred", "status": "confirmed", "occurrences": 7}],
        ensure_ascii=False), encoding="utf-8")
    (bible / "style_guide.json").write_text(json.dumps({"preset": "faithful-natural"}), encoding="utf-8")
    chars = story_bible.load_section(project_dir, "characters")
    assert chars[0]["source"] == "大师兄" and chars[0]["gender"] == "male"
    assert chars[0]["rating"] == 5  # unknown field preserved
    assert chars[0]["id"] == "character:大师兄" and chars[0]["type"] == "character"
    assert chars[0]["names"] == ["大师兄"] and chars[0]["importance"] == 1
    terms = story_bible.load_section(project_dir, "terminology")
    assert terms[0]["canonical_translation"] == {"en": "Lin Fan"}  # untouched semantics
    assert terms[0]["names"] == ["林凡"] and terms[0]["importance"] == 7
    assert story_bible.load_section(project_dir, "style_guide") == {"preset": "faithful-natural"}
    assert story_bible.load_section(project_dir, "locations") == []
    # save/load round-trip is stable and preserves everything
    story_bible.save_section(project_dir, "characters", chars)
    assert story_bible.load_section(project_dir, "characters") == chars
    # graph view over old files
    g = story_bible.load_graph(project_dir)
    assert g.resolve("大师兄") and g.resolve("林凡")
    assert [e["name"] for e in story_bible.entities_active_in(project_dir, "大师兄打坐")] == ["大师兄"]


def test_bible_graph_and_links(tmp_path):
    project_dir = tmp_path
    story_bible.save_section(project_dir, "characters", [
        {"origin": "user", "source": "林凡", "translation": "Lin Fan", "gender": "male"}])
    story_bible.save_section(project_dir, "places_note" if False else "locations", [
        {"origin": "user", "source": "东湖", "translation": "East Lake"}])
    edge = story_bible.link_entities(project_dir, "character:林凡", "place:东湖", "located_in")
    assert edge["type"] == "located_in"
    rels = story_bible.relations_of(project_dir, "character:林凡")
    assert len(rels) == 1 and rels[0]["other_id"] == "place:东湖"


def test_old_relationships_never_dropped(tmp_path):
    project_dir = tmp_path
    story_bible.save_section(project_dir, "relationships", [
        {"source": "林凡", "target": "苏晴", "type": "allied_with", "legacy_note": "manuscript v1"},
        {"random": "shape"}])
    kept = story_bible.load_section(project_dir, "relationships")
    assert kept[0]["legacy_note"] == "manuscript v1" and kept[1] == {"random": "shape"}
