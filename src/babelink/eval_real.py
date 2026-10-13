"""Real-corpus evaluation (spec §57): translate an excerpt of a Chinese novel under experiments A-F and
score each against a published human English translation of the same chapter.

A bare LLM   B +glossary   C +Story Bible   D +translation memory   E +hierarchical context   F +refinement +QA
(each layer includes the ones before it). One fresh project per config, same model, same excerpt.

Alignment is chapter-level (by chapter number in the titles) plus a proportional-length window for the
excerpt, because two translations of the same chapter never split paragraphs identically. chrF therefore
measures overlap with *a* human translation, not agreement with a gold paragraph alignment: compare configs
with each other, not with absolute MT benchmarks.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Optional

from .db import Database
from .eval import chrf, term_adherence
from .importers import import_epub
from .models import SegmentState
from .pipeline import translate_project
from .qa import run_qa
from .refinement import run_refinement
from .segment import ParsedChapter, to_html
from .importers.base import store_chapters
from .summaries import write_summaries
from .terminology import build_glossary, mine_terms, reconcile_glossary, suggest_term_translations, sync_bible, upsert_candidates

CONFIGS = {
    "A": ("bare LLM", frozenset(), 0),
    "B": ("+glossary", frozenset({"glossary"}), 0),
    "C": ("+Story Bible", frozenset({"glossary", "bible"}), 0),
    "D": ("+translation memory", frozenset({"glossary", "bible", "tm"}), 0),
    "E": ("+hierarchical context", frozenset({"glossary", "bible", "tm", "context"}), 0),
    "F": ("full pipeline (+refine +QA)", frozenset({"glossary", "bible", "tm", "context"}), 0),
    "G": ("entity-first placeholders", frozenset({"placeholders", "bible", "tm"}), 0),  # mirrors D: mask swaps hint
    "H": ("piecewise long segments (E13)", frozenset({"glossary", "bible", "tm"}), 300),  # D + split_pieces=300
}
CONFIG_SPLIT = {k: v[2] for k, v in CONFIGS.items()}  # split_pieces per config (0 = off)
_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")
_CN_DIGIT = {c: i for i, c in enumerate("零一二三四五六七八九")} | {"〇": 0}
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _cn_to_int(s: str) -> int:
    total = cur = 0
    for ch in s:
        if ch in _CN_DIGIT:
            cur = _CN_DIGIT[ch]
        elif ch == "十":
            total, cur = total + (cur or 1) * 10, 0
        elif ch == "百":
            total, cur = total + (cur or 1) * 100, 0
    return total + cur


def _roman_to_int(s: str) -> int:
    vals = [_ROMAN[c] for c in s.lower()]
    return sum(-v if i + 1 < len(vals) and v < vals[i + 1] else v for i, v in enumerate(vals))


def chapter_number(title: str) -> Optional[int]:
    """'第三回 …' -> 3, 'CHAPTER III.' -> 3, 'Chapter 12' -> 12."""
    m = re.search(r"第\s*([零〇一二三四五六七八九十百]+|\d+)\s*[章节節回卷]", title)
    if m:
        g = m.group(1)
        return int(g) if g.isdigit() else _cn_to_int(g)
    m = re.search(r"chapter\s+([ivxlcdm]+|\d+)\b", title, re.IGNORECASE)
    if m:
        g = m.group(1)
        return int(g) if g.isdigit() else _roman_to_int(g)
    return None


def find_chapter(db: Database, project_id: int, number: int):
    return next((c for c in db.chapters(project_id) if chapter_number(c["title"]) == number), None)


def align_reference(zh_lens: list[int], k: int, en_paras: list[str]) -> str:
    """English paragraphs covering the same fraction of the chapter as the first k Chinese paragraphs."""
    total = sum(zh_lens) or 1
    upto = sum(zh_lens[:k]) / total
    en_total = sum(len(p) for p in en_paras) or 1
    out, cum = [], 0
    for p in en_paras:
        mid = (cum + len(p) / 2) / en_total
        cum += len(p)
        if mid <= upto:
            out.append(p)
    return "\n".join(out)


def _copy_project(src: Database, src_pid: int, upto_order: int, keep_in_last: int, dst: Database) -> tuple[int, int]:
    """Copy chapters 0..upto_order of a project; the last one keeps only its first `keep_in_last` paragraphs."""
    pid = dst.create_project("eval", source_lang="zh", target_lang="en")
    doc = dst.add_document(pid, "eval", "epub", "eval")
    chapters = []
    for ch in src.chapters(src_pid):
        if ch["order_index"] > upto_order:
            break
        paras = [s["source_text"] for s in src.segments_for_chapter(ch["id"])]
        if ch["order_index"] == upto_order:
            paras = paras[:keep_in_last]
        chapters.append(ParsedChapter(ch["title"], to_html(paras), paras))
    ids = store_chapters(dst, pid, doc, chapters)
    for t in src.terminology_for_project(src_pid):
        dst.conn.execute(
            "INSERT INTO terminology(project_id, source_forms, canonical_translation, concept, policy, status, occurrences) VALUES (?,?,?,?,?,?,?)",
            (pid, t["source_forms"], t["canonical_translation"], t["concept"], t["policy"], t["status"], t["occurrences"]))
    dst.conn.commit()
    for s in dst.conn.execute("SELECT id FROM segments WHERE project_id=?", (pid,)).fetchall():
        dst.update_segment(s["id"], state=SegmentState.ANALYZED)
    return pid, ids[-1]


def run_corpus_eval(zh_epub: str | Path, en_epub: str | Path, provider, *, chapters: list[int], paragraphs: int = 8,
                    configs: str = "ABCDEF", workdir: str | Path, term_limit: int = 30, log=print, term_provider=None,
                    refine_provider=None) -> dict:
    workdir = Path(workdir)
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    zh, en = Database(workdir / "zh.db"), Database(workdir / "en.db")
    zpid, epid = zh.create_project("zh", source_lang="zh"), en.create_project("en", source_lang="en")
    import_epub(zh, zpid, zh_epub)
    import_epub(en, epid, en_epub)
    log(f"imported zh={len(zh.chapters(zpid))} chapters, en={len(en.chapters(epid))} chapters", flush=True)

    all_texts = [s["source_text"] for s in zh.conn.execute("SELECT source_text FROM segments WHERE project_id=?", (zpid,))]
    upsert_candidates(zh, zpid, mine_terms(all_texts, min_count=3, top=1500))
    log(f"mined {len(zh.terminology_for_project(zpid))} term candidates over the whole book", flush=True)

    out: dict = {"model": provider.name, "paragraphs": paragraphs, "configs": {k: CONFIGS[k][0] for k in configs}, "chapters": {}}
    for number in chapters:
        zch, ech = find_chapter(zh, zpid, number), find_chapter(en, epid, number)
        if not zch or not ech:
            log(f"chapter {number}: not found in both books, skipped", flush=True)
            continue
        zsegs = zh.segments_for_chapter(zch["id"])
        k = min(paragraphs, len(zsegs))
        source = [s["source_text"] for s in zsegs[:k]]
        reference = align_reference([len(s["source_text"]) for s in zsegs], k, [s["source_text"] for s in en.segments_for_chapter(ech["id"])])
        other = find_chapter(en, epid, number + 2) or find_chapter(en, epid, max(number - 2, 1))
        floor = chrf("\n".join(s["source_text"] for s in en.segments_for_chapter(other["id"]))[: len(reference)], reference) if other and other["id"] != ech["id"] else None  # a one-chapter book has no unrelated chapter

        # terminology for this excerpt: candidates present in it, classified + translated once, shared by every config
        text = "\n".join(source)
        present = {f for t in zh.terminology_for_project(zpid) for f in json.loads(t["source_forms"]) if f in text}
        t0 = time.time()
        n_tr = suggest_term_translations(zh, zpid, term_provider or provider, only_forms=present, limit=term_limit,
                                         retry_present=present, verbose=True,
                                         translator=provider if getattr(provider, "translation_only", False) else None)
        glossary = {k_: v for k_, v in build_glossary(zh, zpid).items() if k_ in text}
        log(f"chapter {number}: {len(present)} candidate terms in excerpt, {n_tr} translated, {len(glossary)} in glossary ({time.time() - t0:.0f}s)", flush=True)

        res: dict = {"title": zch["title"], "reference": reference, "chrf_floor_unrelated_text": floor,
                     "glossary": glossary, "source": source, "configs": {}}
        for key in configs:
            label, feats, split = CONFIGS[key]
            cdir = workdir / f"ch{number}_{key}"
            cdir.mkdir(parents=True)
            db = Database(cdir / "project.db")
            pid, target_ch = _copy_project(zh, zpid, zch["order_index"], k, db)
            write_summaries(db, pid, str(cdir))
            sync_bible(db, pid, str(cdir))
            t0 = time.time()
            translate_project(db, pid, provider, project_dir=str(cdir), chapter_id=target_ch, features=feats,
                              split_pieces=split)
            if key == "F":
                run_refinement(db, pid, refine_provider or provider, str(cdir), chapter_id=target_ch)
                run_qa(db, pid, build_glossary(db, pid), chapter_id=target_ch)
            secs = time.time() - t0
            segs = db.segments_for_chapter(target_ch)
            hyp = [s["translated_text"] or "" for s in segs]
            hit, tot = term_adherence(text, "\n".join(hyp), glossary)
            qa_rows = db.qa_for_project(pid)
            res["configs"][key] = {
                "label": label, "chrf": round(chrf("\n".join(hyp), reference), 2), "term_hits": hit, "term_total": tot,
                "cjk_leftover_paragraphs": sum(1 for h in hyp if _CJK.search(h)),
                "qa_warnings": sum(1 for r in qa_rows if r["severity"] == "warning"), "qa_errors": sum(1 for r in qa_rows if r["severity"] == "error"),
                "refused": sum(1 for s in segs if s["state"] == SegmentState.NEEDS_REVIEW.value and not s["translated_text"]),
                "seconds": round(secs, 1), "output": hyp, "chars": sum(map(len, hyp)),
            }
            log(f"  {key} {label}: chrF={res['configs'][key]['chrf']} terms={hit}/{tot} {secs:.0f}s", flush=True)
            db.close()
        oracle = "A" if "A" in configs else configs[0]  # uninstructed outputs are the purest habit oracle
        odb = Database(workdir / f"ch{number}_{oracle}" / "project.db")
        opid = odb.conn.execute("SELECT MAX(id) m FROM projects").fetchone()["m"]
        res["elected"] = reconcile_glossary(odb, opid)
        odb.close()
        if res["elected"]:
            log(f"  book vote ({oracle}): {res['elected']}", flush=True)
        out["chapters"][str(number)] = res
    out["mean_chrf"] = {k: round(sum(c["configs"][k]["chrf"] for c in out["chapters"].values() if k in c["configs"]) / max(1, len(out["chapters"])), 2) for k in configs}
    zh.close()
    en.close()
    return out


def harness_score(term_hits: int, term_total: int, cjk_left: int, paras: int, qa_errors: int, refused: int) -> float:
    """0-100 for what the harness controls: terms applied (40), complete
    delivery in the target script (30), QA-clean (20), nothing refused (10)."""
    terms = (term_hits / term_total) if term_total else 1.0
    complete = max(0.0, 1.0 - cjk_left / max(paras, 1))
    qa = 1.0 if qa_errors == 0 else max(0.0, 1.0 - qa_errors / max(paras, 1))
    clean = 0.0 if refused else 1.0
    return round(40 * terms + 30 * complete + 20 * qa + 10 * clean, 1)


def write_corpus_report(result: dict, path: str | Path, title: str = "Real-corpus evaluation") -> Path:
    L = [f"# {title}", "", f"Model: `{result['model']}` · first {result['paragraphs']} paragraphs per chapter · "
         "reference: human English translation aligned by chapter + proportional length.", "",
         "chrF is 0-100 (higher = closer to the human translation). Term adherence = glossary terms in the source that appear "
         "with the agreed rendering. `floor` = chrF of unrelated English text of the same length against the reference. "
         "**Harness Score** (0-100) covers what the harness controls — terms applied, complete delivery, QA-clean, nothing refused — "
         "and is the number that should read near 100.", ""]
    for num, ch in result["chapters"].items():
        L += [f"## Chapter {num}: {ch['title']}", "", f"floor (unrelated text): {ch['chrf_floor_unrelated_text']:.1f}" if ch['chrf_floor_unrelated_text'] is not None else "",
              *(["", f"Book vote (elected renderings): {ch['elected']}"] if ch.get("elected") else []),
              "", "| Config | chrF | Term adherence | Harness Score | CJK left | QA warn/err | Time |", "|---|---|---|---|---|---|---|"]
        for key, c in ch["configs"].items():
            n = max(len(c.get("output", []) or []), 1)
            score = harness_score(c["term_hits"], c["term_total"], c["cjk_leftover_paragraphs"], n, c["qa_errors"], c.get("refused", 0))
            L.append(f"| {key} {c['label']} | {c['chrf']} | {c['term_hits']}/{c['term_total']} | {score} | {c['cjk_leftover_paragraphs']} | {c['qa_warnings']}/{c['qa_errors']} | {c['seconds']}s |")
        L += ["", "Glossary used: " + (", ".join(f"{k}={v}" for k, v in ch["glossary"].items()) or "(none)"), "",
              "<details><summary>First paragraph: source / reference / outputs</summary>", "", f"**Source**: {ch['source'][0]}", "",
              f"**Reference (aligned window, start)**: {ch['reference'][:600]}", ""]
        for key, c in ch["configs"].items():
            L += [f"**{key}**: {(c['output'] or [''])[0]}", ""]
        L += ["</details>", ""]
    if len(result["chapters"]) > 1:
        L += ["## Mean chrF over chapters", "", "| Config | chrF |", "|---|---|"] + [f"| {k} | {v} |" for k, v in result["mean_chrf"].items()] + [""]
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    return out
