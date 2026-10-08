"""Orchestration shared by the CLI and the web UI, so both behave identically."""
from __future__ import annotations

import json
from pathlib import Path

from .db import Database
from .importers import import_any, import_paste
from .pipeline import analyze_project
from .providers.gemini import GeminiProvider
from .providers.base import MockProvider
from .providers.ollama import OllamaProvider
from .providers.openai_compatible import OpenAICompatibleProvider
from .story_bible import project_root

STYLES = ("literal", "faithful-natural", "literary", "highly-literary")
PROVIDERS = ("mock", "ollama", "openai-compatible", "gemini")


def make_provider(kind: str = "mock", model: str = "qwen2.5:7b", base_url: str = ""):
    if kind == "mock":
        return MockProvider()
    if kind == "ollama":
        return OllamaProvider(model=model, **({"base_url": base_url} if base_url else {}))
    if kind == "openai-compatible":
        return OpenAICompatibleProvider(base_url=base_url or "http://localhost:8080/v1", model=model)
    if kind == "gemini":
        return GeminiProvider(model=model or "gemini-2.0-flash")
    raise ValueError(f"unknown provider: {kind}")


def open_workspace(workspace: str | Path) -> Database:
    return Database(Path(workspace) / "project.db")


def create_project(db: Database, name: str, source_lang: str = "auto", target_lang: str = "en", style: str = "faithful-natural") -> int:
    pid = db.create_project(name, source_lang=source_lang, target_lang=target_lang)
    db.set_project(pid, style=style)
    return pid


def import_file(db: Database, pid: int, path: str | Path) -> int:
    n_before = len(db.chapters(pid))
    import_any(db, pid, path)
    return len(db.chapters(pid)) - n_before


def add_pasted_chapter(db: Database, workspace: str | Path, pid: int, text: str, title: str = "Pasted", provider=None) -> list[int]:
    """Append pasted text as new chapter(s) and analyze incrementally: earlier chapters' glossary, TM,
    summaries and Bible entries stay; only new terms are mined and new chapters summarized."""
    before = {c["id"] for c in db.chapters(pid)}
    import_paste(db, pid, text, title)
    analyze_project(db, pid, project_root(workspace, pid), provider=provider)
    return [c["id"] for c in db.chapters(pid) if c["id"] not in before]


def overview(db: Database, pid: int) -> dict:
    """Numbers for the project header / `projects` listing."""
    p = db.get_project(pid)
    states = {r["state"]: r["n"] for r in db.conn.execute("SELECT state, COUNT(*) n FROM segments WHERE project_id=? GROUP BY state", (pid,))}
    total = sum(states.values())
    translated = total - sum(states.get(k, 0) for k in ("UNPROCESSED", "ANALYZED"))
    terms = db.terminology_for_project(pid)
    target = p["target_lang"]
    return {
        "id": pid, "name": p["name"], "source_lang": p["source_lang"], "target_lang": target,
        "style": p["style"], "chapters": len(db.chapters(pid)), "segments": total,
        "pages": db.conn.execute("SELECT COUNT(*) n FROM chapters c JOIN documents d ON d.id=c.document_id WHERE c.project_id=? AND d.format='image'", (pid,)).fetchone()["n"], "translated": translated,
        "states": states,
        "terms": sum(1 for t in terms if t["status"] != "ignored"),
        "terms_with_translation": sum(1 for t in terms if t["status"] != "ignored" and target in json.loads(t["canonical_translation"])),
        "tm_entries": db.conn.execute("SELECT COUNT(*) n FROM translation_memory WHERE project_id=?", (pid,)).fetchone()["n"],
        "summaries": db.conn.execute("SELECT COUNT(*) n FROM chapters WHERE project_id=? AND summary<>''", (pid,)).fetchone()["n"],
        "qa_warnings": db.conn.execute("SELECT COUNT(*) n FROM qa_results WHERE project_id=? AND severity='warning'", (pid,)).fetchone()["n"],
        "qa_errors": db.conn.execute("SELECT COUNT(*) n FROM qa_results WHERE project_id=? AND severity='error'", (pid,)).fetchone()["n"],
    }
