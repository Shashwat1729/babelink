"""storytranslate CLI (spec §85)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .db import Database
from .importers import import_epub, import_txt
from .exporters import export_epub, export_txt
from .pipeline import analyze_project, translate_project
from .providers.base import MockProvider
from .providers.ollama import OllamaProvider
from .providers.openai_compatible import OpenAICompatibleProvider


def _db(args) -> Database:
    return Database(Path(args.project_dir) / "project.db")


def _provider(args):
    if args.provider == "mock":
        return MockProvider()
    if args.provider == "ollama":
        return OllamaProvider(model=args.model)
    if args.provider == "openai-compatible":
        return OpenAICompatibleProvider(base_url=args.base_url, model=args.model)
    raise SystemExit(f"Unknown provider: {args.provider}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="storytranslate")
    p.add_argument("--project-dir", default="project", help="Directory holding project.db")
    sub = p.add_subparsers(dest="command", required=True)

    pi = sub.add_parser("import")
    pi.add_argument("path")
    pi.add_argument("--name", default=None)
    pi.add_argument("--target-lang", default="en")

    pa = sub.add_parser("analyze")
    pa.add_argument("project_id", type=int)

    pt = sub.add_parser("terminology")
    pt.add_argument("project_id", type=int)

    ptr = sub.add_parser("translate")
    ptr.add_argument("project_id", type=int)
    ptr.add_argument("--provider", default="mock", choices=["mock", "ollama", "openai-compatible"])
    ptr.add_argument("--model", default="qwen2.5:7b")
    ptr.add_argument("--base-url", default="http://localhost:8080/v1")
    ptr.add_argument("--limit", type=int, default=None)

    pq = sub.add_parser("qa")
    pq.add_argument("project_id", type=int)

    pe = sub.add_parser("export")
    pe.add_argument("project_id", type=int)
    pe.add_argument("--source", required=True, help="Original document path")
    pe.add_argument("--out", required=True)

    args = p.parse_args(argv)

    if args.command == "import":
        db = _db(args)
        name = args.name or Path(args.path).stem
        pid = db.create_project(name, target_lang=args.target_lang)
        if args.path.lower().endswith(".epub"):
            import_epub(db, pid, args.path)
        else:
            import_txt(db, pid, args.path)
        print(f"Imported project {pid} from {args.path}")
        return 0

    if args.command == "analyze":
        db = _db(args)
        print(analyze_project(db, args.project_id))
        return 0

    if args.command == "terminology":
        db = _db(args)
        for t in db.terminology_for_project(args.project_id):
            print(t["source_forms"], "->", t["canonical_translation"], t["status"], t["occurrences"])
        return 0

    if args.command == "translate":
        db = _db(args)
        n = translate_project(db, args.project_id, _provider(args), limit=args.limit)
        print(f"Translated {n} segments")
        return 0

    if args.command == "qa":
        db = _db(args)
        for row in db.qa_for_project(args.project_id):
            print(row["severity"], row["type"], row["message"])
        return 0

    if args.command == "export":
        db = _db(args)
        out = Path(args.out)
        if out.suffix.lower() == ".epub":
            export_epub(db, args.project_id, args.source, out)
        else:
            export_txt(db, args.project_id, out)
        print(f"Exported to {out}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
