"""lorebridge CLI (spec §85).

One workspace directory (`--project-dir`) holds project.db and, per project, a story_bible/ folder.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, service
from .db import Database
from .exporters import export_epub, export_txt
from .pipeline import analyze_project, translate_project
from .story_bible import project_root

PROV = argparse.ArgumentParser(add_help=False)
PROV.add_argument("--provider", choices=service.PROVIDERS, help="default: config file, else mock")
PROV.add_argument("--model", help="default: config file (models.<command> or model), else Hy-MT2-1.8B")
PROV.add_argument("--helper-model", help="general LLM for terms/refine/review/summaries when --model is translation-only (Hy-MT2)")
PROV.add_argument("--base-url")


def _db(args) -> Database:
    Path(args.project_dir).mkdir(parents=True, exist_ok=True)
    return service.open_workspace(args.project_dir)


def _provider(args, source_lang: str = "", target_lang: str = "en"):
    kind, model = args.provider, args.model
    if kind == "auto":  # direction-aware default engine (evidence: bake-off E04, gold-glossary E07)
        kind, auto_model = service.default_engine(source_lang, target_lang)
        model = model or auto_model
    return service.make_provider(kind, model, args.base_url)


def _translator(args, main=None, source_lang: str = "", target_lang: str = "en"):
    """Translation engine with failover (Hy-MT2, then the helper model
    degraded). Eval/benchmark commands keep using _provider for single-model
    purity; user-facing translate/retranslate go through the chain so one
    dead model never aborts a book."""
    from .fallback import resolve_chains

    main = main if main is not None else _provider(args, source_lang, target_lang)

    def make(kind, model="", base_url=""):
        if kind == args.provider and (model or "") == (args.model or ""):
            return main  # reuse the instance the helper was derived from
        return service.make_provider(kind, model, base_url)

    chain, _ = resolve_chains(args.provider, args.model, args.base_url,
                              getattr(args, "helper_model", "") or "", make=make,
                              source_lang=source_lang, target_lang=target_lang)
    return chain


def _helper(args, main=None):
    """General-LLM helper for non-translation tasks. Same object as main for
    general LLMs; a helper model when main is translation-only (Hy-MT2)."""
    main = main if main is not None else _provider(args)
    return service.helper_for(main, args.provider, args.base_url, getattr(args, "helper_model", "") or "")


def _chapter_id(db: Database, pid: int, number):
    """--chapter N is the 1-based position in reading order."""
    if number is None:
        return None
    chapters = db.chapters(pid)
    if not 1 <= number <= len(chapters):
        raise SystemExit(f"chapter {number} out of range (1..{len(chapters)})")
    return chapters[number - 1]["id"]


def _progress(done: int, total: int, text: str) -> None:
    print(f"\r[{done}/{total}] {text[:50]:<50}", end="", file=sys.stderr, flush=True)
    if done == total:
        print(file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lorebridge")
    p.add_argument("--project-dir", default="project", help="Workspace directory (project.db + story bibles)")
    p.add_argument("--config", help="YAML config (default: <project-dir>/lorebridge.yaml if present)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("projects", help="List projects")

    pi = sub.add_parser("import", help="Create a project from an EPUB/TXT/HTML file, a comic page image or a CBZ")
    pi.add_argument("path")
    pi.add_argument("--name")
    pi.add_argument("--source-lang")
    pi.add_argument("--target-lang")
    pi.add_argument("--style", choices=service.STYLES)

    pp = sub.add_parser("paste", help="Add pasted text as new chapter(s) to an existing project", parents=[PROV])
    pp.add_argument("project_id", type=int)
    pp.add_argument("--file", help="Text file; omit to read stdin")
    pp.add_argument("--title", default="Pasted")
    pp.add_argument("--translate", action="store_true", help="Translate the new chapter right away")

    pc = sub.add_parser("chapters", help="Chapter list with progress")
    pc.add_argument("project_id", type=int)

    pa = sub.add_parser("analyze", parents=[PROV], help="Detect language, mine terms, summaries; with a real provider also classify+translate terms")
    pa.add_argument("project_id", type=int)
    pa.add_argument("--suggest", action="store_true", help="Ask the model to classify and translate term candidates")

    pt = sub.add_parser("terminology", parents=[PROV], help="List / review terms")
    pt.add_argument("project_id", type=int)
    pt.add_argument("--accept", type=int, metavar="TERM_ID")
    pt.add_argument("--ignore", type=int, metavar="TERM_ID")
    pt.add_argument("--edit", type=int, metavar="TERM_ID")
    pt.add_argument("--translation", help="New rendering for --edit")
    pt.add_argument("--accept-all", action="store_true")

    ptr = sub.add_parser("translate", parents=[PROV])
    ptr.add_argument("project_id", type=int)
    ptr.add_argument("--chapter", type=int, help="Only this chapter (1-based position)")
    ptr.add_argument("--limit", type=int)
    ptr.add_argument("--words", type=int, help="Stop after the paragraph that brings this run to N source words (CJK: one per character)")
    ptr.add_argument("--summaries", action="store_true", default=None, help="Write a model summary after each finished chapter")
    ptr.add_argument("--n-prev", type=int, help="Previous translated passages shown to the model (default 3)")
    ptr.add_argument("--escalate-model", help="Stronger Ollama model for risky segments (spec §10)")
    ptr.add_argument("--candidates", type=int, default=1, help="Model draws per paragraph; the deterministic best wins (slower, best quality)")

    pq = sub.add_parser("qa", help="Run deterministic QA and list findings")
    pq.add_argument("project_id", type=int)
    pq.add_argument("--chapter", type=int)

    pr = sub.add_parser("refine", parents=[PROV])
    pr.add_argument("project_id", type=int)
    pr.add_argument("--chapter", type=int)
    pr.add_argument("--limit", type=int)

    pv = sub.add_parser("review", parents=[PROV], help="AI QA pass")
    pv.add_argument("project_id", type=int)
    pv.add_argument("--chapter", type=int)

    pe = sub.add_parser("edit", help="Replace a translation by hand (locks it as HUMAN_APPROVED)")
    pe.add_argument("project_id", type=int)
    pe.add_argument("segment_id", type=int)
    pe.add_argument("--text", required=True)
    pe.add_argument("--scope", default="local", choices=["local", "project"])

    pap = sub.add_parser("approve", help="Approve a translation as-is")
    pap.add_argument("project_id", type=int)
    pap.add_argument("segment_id", type=int)

    pb = sub.add_parser("storybible")
    pb.add_argument("section", nargs="?")
    pb.add_argument("--project-id", type=int, default=1)

    ps = sub.add_parser("style")
    ps.add_argument("preset", choices=service.STYLES)
    ps.add_argument("--project-id", type=int, default=1)

    prt = sub.add_parser("retranslate", parents=[PROV], help="Change a term's rendering and redo affected segments")
    prt.add_argument("project_id", type=int)
    prt.add_argument("--term", required=True)
    prt.add_argument("--new", required=True)
    prt.add_argument("--dry-run", action="store_true", help="Only show what would go stale, change nothing")

    prg = sub.add_parser("retarget", help="New target language from the same source: story graph (term forms/concepts) carries over, renderings are re-done")
    prg.add_argument("project_id", type=int)
    prg.add_argument("--target-lang", required=True)
    prg.add_argument("--name", required=True)

    pval = sub.add_parser("validate", help="Check a project / exported EPUB is complete (spec §84)")
    pval.add_argument("project_id", type=int)
    pval.add_argument("--file", help="Exported .epub to inspect as well")

    px = sub.add_parser("export")
    px.add_argument("project_id", type=int)
    px.add_argument("--source", help="Original EPUB to write translations back into (optional)")
    px.add_argument("--out", required=True)
    px.add_argument("--bilingual", action="store_true")

    pw = sub.add_parser("web")
    pw.add_argument("--host", default="127.0.0.1")
    pw.add_argument("--port", type=int, default=8000)

    pm = sub.add_parser("models")
    pm.add_argument("--target-lang", default="en")
    pm.add_argument("--quality", default="balanced", choices=["fast", "balanced", "quality", "max"])

    sub.add_parser("benchmark", parents=[PROV])

    pbm = sub.add_parser("benchmark-run", parents=[PROV])
    pbm.add_argument("dataset")
    pbm.add_argument("--out", default="leaderboard.md")

    pmx = sub.add_parser("matrix", parents=[PROV])
    pmx.add_argument("dataset")
    pmx.add_argument("--out", default="matrix.md")

    pev = sub.add_parser("eval-epub", parents=[PROV], help="Experiments A-F: translate chapters of a Chinese EPUB, score against a human English EPUB")
    pev.add_argument("zh_epub")
    pev.add_argument("en_epub")
    pev.add_argument("--chapters", default="3", help="Comma-separated chapter numbers present in both books")
    pev.add_argument("--paragraphs", type=int, default=8)
    pev.add_argument("--configs", default="ABCDEFG")
    pev.add_argument("--out", default="", help="Report path (default: <project-dir>/eval_report.md; never a tracked doc)")
    pev.add_argument("--json", default="")

    pd = sub.add_parser("datasets", help="Dataset license manifest (spec §46)")
    pd.add_argument("--check", action="store_true", help="Exit 1 if a dataset lacks license fields")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # a legacy Windows console (cp1252) cannot print the CJK in names and source text
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    cmd = args.command
    config.apply(args, config.load(args.config or Path(args.project_dir) / "lorebridge.yaml", required=bool(args.config)))

    if cmd == "web":
        import os

        import uvicorn

        os.environ["LOREBRIDGE_HOME"] = str(Path(args.project_dir).resolve())
        uvicorn.run("lorebridge.web:app", host=args.host, port=args.port, reload=False)
        return 0

    if cmd == "models":
        from .hardware import detect
        from .registry import recommend

        hw = detect()
        print(f"Hardware: {hw}")
        for m in recommend(args.target_lang, hw["vram_gb"], hw["ram_gb"], quality=args.quality):
            print(f"- {m.name} ({m.provider}/{m.backend}) ram~{m.ram_gb}GB vram~{m.vram_gb}GB tasks={m.recommended_tasks}")
        return 0

    if cmd == "benchmark":
        from .benchmark import benchmark

        print(benchmark(_provider(args)))
        return 0

    if cmd in ("benchmark-run", "matrix"):
        from .datasets import LocalFileAdapter
        from .eval import run_benchmark, run_matrix, write_leaderboard, write_matrix_leaderboard

        records = LocalFileAdapter(args.dataset).load()
        if cmd == "matrix":
            print(write_matrix_leaderboard(run_matrix(records, _provider(args), args.project_dir), args.out))
        else:
            result = run_benchmark(records, _provider(args))
            print(result)
            print(write_leaderboard([result], args.out))
        return 0

    if cmd == "eval-epub":
        from .eval_real import run_corpus_eval, write_corpus_report

        main = _provider(args)
        result = run_corpus_eval(args.zh_epub, args.en_epub, main, chapters=[int(x) for x in args.chapters.split(",")],
                                 paragraphs=args.paragraphs, configs=args.configs, workdir=Path(args.project_dir) / "eval_run",
                                 term_provider=_helper(args, main), refine_provider=_helper(args, main))
        if args.json:
            Path(args.json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        out = args.out or str(Path(args.project_dir) / "eval_report.md")
        print(write_corpus_report(result, out))
        print("mean chrF:", result["mean_chrf"])
        return 0

    if cmd == "datasets":
        from .dataset_manifest import check_manifest, format_manifest

        print(format_manifest())
        problems = check_manifest()
        for pr_ in problems:
            print("PROBLEM:", pr_, file=sys.stderr)
        return 1 if (args.check and problems) else 0

    db = _db(args)
    root = lambda: project_root(args.project_dir, args.project_id)  # noqa: E731

    if cmd == "projects":
        for p in db.projects():
            o = service.overview(db, p["id"])
            print(f"{o['id']:>3}  {o['name']}  [{o['source_lang']}->{o['target_lang']}, {o['style']}]  chapters={o['chapters']} "
                  f"translated={o['translated']}/{o['segments']} terms={o['terms']} tm={o['tm_entries']}")
        return 0

    if cmd == "import":
        name = args.name or Path(args.path).stem
        pid = service.create_project(db, name, args.source_lang, args.target_lang, args.style)
        n = service.import_file(db, pid, args.path)
        print(f"Imported project {pid} '{name}': {n} chapters")
        return 0

    pid = getattr(args, "project_id", None)
    if pid is not None and db.get_project(pid) is None:
        raise SystemExit(f"no project {pid} in {args.project_dir}")

    if cmd == "paste":
        text = Path(args.file).read_text(encoding="utf-8-sig") if args.file else sys.stdin.read()
        if not text.strip():
            raise SystemExit("nothing to paste")
        provider = _provider(args)
        new = service.add_pasted_chapter(db, args.project_dir, pid, text, args.title, provider=None if args.provider == "mock" else provider)
        print(f"Added {len(new)} chapter(s) to project {pid}; glossary/TM/summaries from earlier chapters will be reused")
        if args.translate:
            n = sum(translate_project(db, pid, provider, project_dir=project_root(args.project_dir, pid), chapter_id=c, on_progress=_progress, n_prev=args.n_prev) for c in new)
            print(f"Translated {n} segments")
        return 0

    if cmd == "chapters":
        for c in db.chapter_progress(pid):
            done = c["total"] - c["states"].get("UNPROCESSED", 0) - c["states"].get("ANALYZED", 0)
            print(f"{c['order'] + 1:>4}  {done:>3}/{c['total']:<3}  {c['title'][:60]}" + (f"  -> {c['title_translated']}" if c["title_translated"] else ""))
        return 0

    if cmd == "analyze":
        use = None if args.provider == "mock" and not args.suggest else _provider(args)
        helper = None if use is None else _helper(args, use)
        print(analyze_project(db, pid, project_dir=project_root(args.project_dir, pid), provider=use, term_provider=helper))
        return 0

    if cmd == "terminology":
        from .terminology import set_term

        if args.accept_all:
            for t in db.terminology_for_project(pid):
                if t["status"] == "suggested" and json.loads(t["canonical_translation"]):
                    db.update_terminology(t["id"], status="confirmed")
        if args.accept:
            db.update_terminology(args.accept, status="confirmed")
        if args.ignore:
            db.update_terminology(args.ignore, status="ignored")
        if args.edit:
            if not args.translation:
                raise SystemExit("--edit needs --translation")
            set_term(db, pid, args.edit, translation=args.translation)
        target = db.get_project(pid)["target_lang"]
        for t in db.terminology_for_project(pid):
            print(f"{t['id']:>4}  {'/'.join(json.loads(t['source_forms'])):<12} -> {json.loads(t['canonical_translation']).get(target, '?'):<24} {t['status']:<10} {t['concept']:<12} x{t['occurrences']}")
        return 0

    if cmd == "translate":
        escalate = service.make_provider("ollama", args.escalate_model) if args.escalate_model else None
        main = _provider(args)
        proj = db.get_project(pid)
        n = translate_project(db, pid, _translator(args, main, proj["source_lang"], proj["target_lang"]), limit=args.limit, project_dir=project_root(args.project_dir, pid),
                              chapter_id=_chapter_id(db, pid, args.chapter), on_progress=_progress, summarize=args.summaries, escalate_provider=escalate,
                              n_prev=args.n_prev, word_limit=args.words, summarize_provider=_helper(args, main),
                              candidates=getattr(args, "candidates", 1) or 1)
        print(f"Translated {n} segments")
        return 0

    if cmd == "qa":
        from .qa import run_qa
        from .terminology import build_glossary

        print(run_qa(db, pid, build_glossary(db, pid), _chapter_id(db, pid, args.chapter)))
        for row in db.qa_for_project(pid):
            print(f"seg {row['segment_id']}: {row['severity']} {row['type']} {row['message']}")
        return 0

    if cmd == "refine":
        from .refinement import run_refinement

        main = _provider(args)
        n = run_refinement(db, pid, _helper(args, main), project_root(args.project_dir, pid), limit=args.limit,
                           chapter_id=_chapter_id(db, pid, args.chapter), on_progress=_progress)
        print(f"Refined {n} segments")
        return 0

    if cmd == "review":
        from .qa import ai_qa

        main = _provider(args)
        print(f"Reviewed {ai_qa(db, pid, _helper(args, main), chapter_id=_chapter_id(db, pid, args.chapter))} segments")
        return 0

    if cmd in ("edit", "approve"):
        from .review import approve_segment, edit_segment

        if cmd == "edit":
            edit_segment(db, pid, args.segment_id, args.text, args.scope)
        else:
            approve_segment(db, pid, args.segment_id)
        print(f"Segment {args.segment_id} -> HUMAN_APPROVED")
        return 0

    if cmd == "storybible":
        from . import story_bible

        if args.section:
            print(json.dumps(story_bible.load_section(root(), args.section), ensure_ascii=False, indent=2)[:6000])
        else:
            print("sections:", ", ".join(story_bible.SECTIONS))
        return 0

    if cmd == "style":
        from . import story_bible

        db.set_project(args.project_id, style=args.preset)
        story_bible.save_section(root(), "style_guide", {"preset": args.preset})
        print(f"Style preset for project {args.project_id}: {args.preset}")
        return 0

    if cmd == "retranslate":
        from .refinement import mark_stale_by_term, propagation_impact

        if args.dry_run:
            impact = propagation_impact(db, pid, args.term)
            print(f"{impact['segments']} segments in {len(impact['chapters'])} chapters would go stale:")
            for title, n in list(impact["chapters"].items())[:10]:
                print(f"  {n}x {title[:60]}")
            return 0
        n = mark_stale_by_term(db, pid, args.term, args.new)
        print(f"Marked {n} segments stale; retranslating")
        main = _provider(args)
        proj = db.get_project(pid)
        print(f"Retranslated {translate_project(db, pid, _translator(args, main, proj['source_lang'], proj['target_lang']), project_dir=project_root(args.project_dir, pid), on_progress=_progress, n_prev=args.n_prev, summarize_provider=_helper(args, main))} segments")
        return 0

    if cmd == "retarget":
        npid = service.retarget(db, pid, args.target_lang, args.name)
        print(f"Project {npid} '{args.name}': same source, target {args.target_lang}, "
              f"{len(db.chapters(npid))} chapters, story graph inherited (renderings to be re-done)")
        return 0

    if cmd == "validate":
        from .exporters.validate import validate_epub, validate_project

        report = validate_project(db, pid)
        if args.file:
            report["epub"] = validate_epub(args.file, db.get_project(pid)["target_lang"])
            report["ok"] = report["ok"] and report["epub"]["ok"]
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1

    if cmd == "export":
        out = Path(args.out)
        if out.suffix.lower() == ".cbz":
            from .images import export_pages
            export_pages(db, pid, out)
        elif out.suffix.lower() == ".epub":
            export_epub(db, pid, args.source, out, bilingual=args.bilingual)
        else:
            export_txt(db, pid, out, bilingual=args.bilingual)
        print(f"Exported to {out}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
